import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.public import cancel_order
from app.bot.keyboards import cart_keyboard, item_draft_keyboard
from app.db import Base
from app.domain import OrderStatus
from app.models import MenuItem
from app.services.orders import (
    ConflictError,
    add_to_cart,
    get_active_event,
    get_cart,
    get_order,
    list_event_menu,
    remove_cart_item,
    reopen_order_for_edit,
    submit_cart,
    transition_order,
    update_cart_item_quantity,
    upsert_user,
)
from app.services.seed import seed_demo_data


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as database_session:
        await seed_demo_data(database_session)
        yield database_session
    await engine.dispose()


async def guest_and_menu(session):
    user = await upsert_user(session, 101, "Guest")
    event = await get_active_event(session)
    entries = await list_event_menu(session, event.id)
    return user, event, entries


async def test_cart_quantity_update_and_delete_are_optimistic(session) -> None:
    user, event, entries = await guest_and_menu(session)
    cart = await add_to_cart(session, user.id, event.id, entries[0].menu_item_id, 1)
    item_id = cart.items[0].id

    cart = await update_cart_item_quantity(
        session,
        user.id,
        item_id,
        2,
        expected_quantity=1,
    )
    assert cart.items[0].quantity == 2

    with pytest.raises(ConflictError, match="Корзина уже изменилась"):
        await update_cart_item_quantity(
            session,
            user.id,
            item_id,
            3,
            expected_quantity=1,
        )
    with pytest.raises(ConflictError, match="Корзина уже изменилась"):
        await remove_cart_item(session, user.id, item_id, expected_quantity=1)

    await remove_cart_item(session, user.id, item_id, expected_quantity=2)
    assert not (await get_cart(session, user.id, event.id)).items


async def test_reopen_submitted_order_merges_matching_cart_lines(session) -> None:
    user, event, entries = await guest_and_menu(session)
    menu_item_id = entries[0].menu_item_id
    await add_to_cart(session, user.id, event.id, menu_item_id, 2)
    order = await submit_cart(session, user.id, event.id)
    await add_to_cart(session, user.id, event.id, menu_item_id, 1)

    cancelled_order, cart = await reopen_order_for_edit(
        session,
        order.id,
        user.id,
        expected_version=order.version,
        actor="guest:101",
    )

    assert cancelled_order.status == OrderStatus.CANCELLED.value
    assert cancelled_order.version == 2
    assert len(cart.items) == 1
    assert cart.items[0].quantity == 3
    assert cart.items[0].menu_item.name_en == "Mojito"

    replacement = await submit_cart(session, user.id, event.id)
    assert replacement.status == OrderStatus.SUBMITTED.value
    assert replacement.public_number != cancelled_order.public_number


async def test_reopen_rejects_combined_cart_over_limit_without_data_loss(session) -> None:
    user, event, entries = await guest_and_menu(session)
    menu_item_id = entries[0].menu_item_id
    await add_to_cart(session, user.id, event.id, menu_item_id, 3)
    order = await submit_cart(session, user.id, event.id)
    cart = await add_to_cart(session, user.id, event.id, menu_item_id, 1)
    cart_item_id = cart.items[0].id

    with pytest.raises(ConflictError, match="Уменьшите количество"):
        await reopen_order_for_edit(
            session,
            order.id,
            user.id,
            expected_version=order.version,
            actor="guest:101",
        )

    unchanged_order = await get_order(session, order.id)
    unchanged_cart = await get_cart(session, user.id, event.id)
    assert unchanged_order.status == OrderStatus.SUBMITTED.value
    assert unchanged_order.version == 1
    assert [(item.id, item.quantity) for item in unchanged_cart.items] == [(cart_item_id, 1)]


async def test_reopen_rejects_stale_version_without_data_loss(session) -> None:
    user, event, entries = await guest_and_menu(session)
    await add_to_cart(session, user.id, event.id, entries[0].menu_item_id, 1)
    order = await submit_cart(session, user.id, event.id)

    with pytest.raises(ConflictError, match="Заказ уже изменился"):
        await reopen_order_for_edit(
            session,
            order.id,
            user.id,
            expected_version=order.version + 1,
            actor="guest:101",
        )

    assert (await get_order(session, order.id)).status == OrderStatus.SUBMITTED.value
    assert not (await get_cart(session, user.id, event.id)).items


async def test_accepted_order_cannot_be_reopened(session) -> None:
    user, event, entries = await guest_and_menu(session)
    await add_to_cart(session, user.id, event.id, entries[0].menu_item_id, 1)
    order = await submit_cart(session, user.id, event.id)
    accepted = await transition_order(
        session,
        order.id,
        OrderStatus.ACCEPTED,
        actor="bartender:test",
        expected_version=order.version,
    )

    with pytest.raises(ConflictError, match="Бармен уже принял"):
        await reopen_order_for_edit(
            session,
            accepted.id,
            user.id,
            expected_version=accepted.version,
            actor="guest:101",
        )

    assert (await get_order(session, accepted.id)).status == OrderStatus.ACCEPTED.value
    assert not (await get_cart(session, user.id, event.id)).items


async def test_public_guest_cancel_rejects_accepted_order(session) -> None:
    user, event, entries = await guest_and_menu(session)
    await add_to_cart(session, user.id, event.id, entries[0].menu_item_id, 1)
    order = await submit_cart(session, user.id, event.id)
    accepted = await transition_order(
        session,
        order.id,
        OrderStatus.ACCEPTED,
        actor="bartender:test",
        expected_version=order.version,
    )

    with pytest.raises(HTTPException) as error:
        await cancel_order(
            accepted.id,
            user.telegram_id,
            session,
            expected_version=accepted.version,
        )

    assert error.value.status_code == 409
    assert (await get_order(session, accepted.id)).status == OrderStatus.ACCEPTED.value


def test_quantity_keyboards_disable_boundary_buttons() -> None:
    item = MenuItem(id=10, category_id=20, name_ru="Тест", name_en="Test")

    minimum = item_draft_keyboard(item, set(), 1, "ru", max_quantity=3)
    maximum = item_draft_keyboard(item, set(), 3, "ru", max_quantity=3)
    assert [button.callback_data for button in minimum.inline_keyboard[0]] == [
        "noop",
        "noop",
        "qty:plus",
    ]
    assert [button.callback_data for button in maximum.inline_keyboard[0]] == [
        "qty:minus",
        "noop",
        "noop",
    ]

    cart = cart_keyboard(
        "en",
        [
            {"id": 1, "quantity": 1, "name": "One"},
            {"id": 2, "quantity": 3, "name": "Three"},
        ],
        max_quantity=3,
    )
    assert cart.inline_keyboard[0][0].callback_data == "noop"
    assert cart.inline_keyboard[1][2].callback_data == "noop"
