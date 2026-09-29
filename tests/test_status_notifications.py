import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import staff
from app.db import Base
from app.domain import OrderStatus, SpecialRequestStatus
from app.models import Order, SpecialRequest
from app.schemas import OrderStatusUpdate, SpecialRequestStatusUpdate
from app.services.orders import (
    add_to_cart,
    create_special_request,
    get_active_event,
    list_event_menu,
    submit_cart,
    upsert_user,
)
from app.services.seed import seed_demo_data
from app.services.telegram_notifications import replace_status_message


@pytest.fixture
async def setup():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_demo_data(session)
        user = await upsert_user(session, 101, "Guest")
        event = await get_active_event(session)
        item = (await list_event_menu(session, event.id))[0]
        await add_to_cart(session, user.id, event.id, item.menu_item_id, 1)
        order = await submit_cart(session, user.id, event.id)
        special = await create_special_request(session, user.id, event.id, "Unknown drink")
        yield SimpleNamespace(session=session, order=order, special=special)
    await engine.dispose()


def request_with_bot(bot):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=bot)))


async def test_order_status_replaces_only_earlier_status_message(setup):
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=[SimpleNamespace(message_id=10), SimpleNamespace(message_id=11),
                         SimpleNamespace(message_id=12)]
        ),
        delete_message=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    request = request_with_bot(bot)
    version = setup.order.version
    for status in (OrderStatus.ACCEPTED, OrderStatus.PREPARING, OrderStatus.READY):
        result = await staff.update_order_status(
            setup.order.id,
            OrderStatusUpdate(status=status, expected_version=version),
            request,
            setup.session,
            "bartender",
        )
        version = result["version"]
    assert bot.send_message.await_count == 3
    assert [call.args for call in bot.delete_message.await_args_list] == [
        (101, 10), (101, 11)
    ]
    assert bot.edit_message_text.await_count == 0
    stored = await setup.session.get(Order, setup.order.id)
    assert stored.status_notification_message_id == 12


async def test_special_request_status_message_is_replaced(setup):
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=[SimpleNamespace(message_id=20), SimpleNamespace(message_id=21)]
        ),
        delete_message=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    request = request_with_bot(bot)
    for status in (SpecialRequestStatus.ACCEPTED, SpecialRequestStatus.FULFILLED):
        await staff.update_special_request(
            setup.special.id,
            SpecialRequestStatusUpdate(status=status),
            request,
            setup.session,
            "bartender",
        )
    bot.delete_message.assert_awaited_once_with(101, 20)
    stored = await setup.session.get(SpecialRequest, setup.special.id)
    assert stored.status_notification_message_id == 21


async def test_old_status_is_edited_if_telegram_cannot_delete_it():
    bot = SimpleNamespace(
        delete_message=AsyncMock(side_effect=RuntimeError("too old")),
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=43)),
    )
    result = await replace_status_message(bot, 101, 42, "Ready")
    assert result == 42
    bot.edit_message_text.assert_awaited_once_with("Ready", chat_id=101, message_id=42)
    bot.send_message.assert_awaited_once_with(101, "Ready")


async def test_failed_send_preserves_and_updates_previous_status():
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=RuntimeError("network timeout")),
        delete_message=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    result = await replace_status_message(bot, 101, 42, "Ready")
    assert result == 42
    bot.delete_message.assert_not_awaited()
    bot.edit_message_text.assert_awaited_once_with("Ready", chat_id=101, message_id=42)


async def test_outdated_transition_does_not_send_status(setup):
    bot = SimpleNamespace(
        send_message=AsyncMock(), delete_message=AsyncMock(), edit_message_text=AsyncMock()
    )
    await staff._notify_status(
        setup.session,
        bot,
        Order,
        setup.order.id,
        OrderStatus.ACCEPTED.value,
        "Already accepted",
        expected_version=setup.order.version + 1,
    )
    bot.send_message.assert_not_awaited()


async def test_sqlite_status_updates_serialize_while_sending(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'orders.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_demo_data(session)
        user = await upsert_user(session, 101, "Guest")
        event = await get_active_event(session)
        item = (await list_event_menu(session, event.id))[0]
        await add_to_cart(session, user.id, event.id, item.menu_item_id, 1)
        order = await submit_cart(session, user.id, event.id)

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    sent = 0

    async def send_message(chat_id, text):
        nonlocal sent
        sent += 1
        if sent == 1:
            first_started.set()
            await release_first.wait()
        return SimpleNamespace(message_id=100 + sent)

    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=send_message),
        delete_message=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    request = request_with_bot(bot)
    try:
        async with factory() as first_session, factory() as second_session:
            first = asyncio.create_task(
                staff.update_order_status(
                    order.id,
                    OrderStatusUpdate(status=OrderStatus.ACCEPTED, expected_version=1),
                    request,
                    first_session,
                    "bartender",
                )
            )
            await asyncio.wait_for(first_started.wait(), 5)
            second = asyncio.create_task(
                staff.update_order_status(
                    order.id,
                    OrderStatusUpdate(status=OrderStatus.PREPARING, expected_version=2),
                    request,
                    second_session,
                    "bartender",
                )
            )
            await asyncio.sleep(0.1)
            assert sent == 1
            async with factory() as unrelated_session:
                await asyncio.wait_for(upsert_user(unrelated_session, 202, "Another guest"), 2)
            release_first.set()
            await asyncio.gather(first, second)
        bot.delete_message.assert_awaited_once_with(101, 101)
    finally:
        release_first.set()
        await engine.dispose()
