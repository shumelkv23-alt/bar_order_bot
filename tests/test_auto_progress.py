import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.domain import OrderStatus
from app.models import Order, utc_now
from app.services.auto_progress import process_due_orders
from app.services.auto_progress_policy import stage_delay
from app.services.orders import (
    ConflictError,
    add_to_cart,
    analytics_summary,
    confirm_auto_closed_order,
    get_active_event,
    list_event_menu,
    list_staff_orders,
    order_to_dict,
    prep_batches,
    submit_cart,
    transition_order,
    upsert_user,
)
from app.services.seed import seed_demo_data


def test_queue_load_changes_stage_deadlines():
    for status in (
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.PREPARING,
        OrderStatus.READY,
    ):
        assert stage_delay(status.value, 3) < stage_delay(status.value, 25)
    assert stage_delay(OrderStatus.PREPARING.value, 3, 3) > stage_delay(
        OrderStatus.PREPARING.value, 3, 1
    )


def test_prep_batches_only_groups_identical_drinks_in_distinct_active_orders():
    def item(modifiers=None, comment="", quantity=1):
        return SimpleNamespace(
            menu_item_id=7,
            name_ru_snapshot="Мохито",
            modifiers_snapshot=modifiers or [],
            comment=comment,
            quantity=quantity,
        )

    def order(id, status, items):
        return SimpleNamespace(
            id=id,
            public_number=f"B-{id:03d}",
            status=status,
            comment="",
            items=items,
            created_at=utc_now(),
        )

    rows = [
        order(1, OrderStatus.SUBMITTED.value, [item(quantity=2)]),
        order(2, OrderStatus.PREPARING.value, [item(comment="без льда")]),
        order(3, OrderStatus.READY.value, [item()]),
        order(4, OrderStatus.ACCEPTED.value, [item()]),
    ]
    batches = prep_batches(rows)
    assert len(batches) == 1
    assert batches[0]["quantity"] == 3
    assert [entry["number"] for entry in batches[0]["orders"]] == ["B-001", "B-004"]
    assert prep_batches([order(1, OrderStatus.SUBMITTED.value, [item(), item()])]) == []
    rows[3].comment = "без мяты"
    rows[0].comment = ""
    rows[1].comment = ""
    rows[2].comment = ""
    assert prep_batches(rows) == []


@pytest.fixture
async def order_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'auto.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_demo_data(session)
        user = await upsert_user(session, 404, "Guest")
        event = await get_active_event(session)
        item = (await list_event_menu(session, event.id))[0]
        await add_to_cart(session, user.id, event.id, item.menu_item_id, 1)
        order = await submit_cart(session, user.id, event.id)
        order_id = order.id
    yield factory, order_id
    await engine.dispose()


async def test_due_order_advances_once_per_poll_and_closes_without_claiming_collection(order_db):
    factory, order_id = order_db
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=lambda _chat, _text: SimpleNamespace(message_id=100)),
        delete_message=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    for expected in (OrderStatus.ACCEPTED, OrderStatus.PREPARING):
        now = utc_now()
        async with factory() as session:
            order = await session.get(Order, order_id)
            order.next_transition_at = now - timedelta(seconds=1)
            await session.commit()
        assert await process_due_orders(factory, bot, now=now) == 1
        async with factory() as session:
            order = await session.get(Order, order_id)
            assert order.status == expected.value
            assert order.status_automatically is True
            assert order.completed_automatically is False
            view = order_to_dict(order)
            assert view["progress_started_at"] is not None
            assert view["progress_started_at"] < view["next_transition_at"]
        assert await process_due_orders(factory, bot, now=now) == 0
    now = utc_now()
    async with factory() as session:
        order = await session.get(Order, order_id)
        order.next_transition_at = now - timedelta(seconds=1)
        await session.commit()
    assert await process_due_orders(factory, bot, now=now) == 0
    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order.status == OrderStatus.PREPARING.value
        ready = await transition_order(
            session, order_id, OrderStatus.READY, actor="bartender", expected_version=order.version
        )
        assert ready.status_automatically is False
        ready.next_transition_at = utc_now() - timedelta(seconds=1)
        await session.commit()
    assert await process_due_orders(factory, bot, now=utc_now()) == 1
    assert bot.send_message.await_count == 3
    assert "автоматически переведён" in bot.send_message.await_args_list[0].args[1]
    assert "расчётному этапу" in bot.send_message.await_args_list[1].args[1]
    assert all(
        "Покажите этот номер" not in call.args[1]
        for call in bot.send_message.await_args_list
    )
    assert "закрыт по таймеру" in bot.send_message.await_args.args[1]
    async with factory() as session:
        order = await session.get(Order, order_id)
        listed = await list_staff_orders(
            session, order.event_id, [OrderStatus.READY.value], include_auto_closed=True
        )
        assert [row.id for row in listed] == [order_id]
        summary = await analytics_summary(session, order.event_id)
        assert summary["completed_orders"] == 0
        assert summary["auto_closed_orders"] == 1
        assert summary["average_fulfillment_minutes"] is None
        order.completed_at = utc_now() - timedelta(minutes=8)
        timer_closed_at = order.completed_at
        await session.commit()
        verification_started = utc_now()
        verified = await confirm_auto_closed_order(
            session, order_id, actor="bartender", expected_version=order.version
        )
        assert verified.completed_automatically is False
        assert verified.completed_at > timer_closed_at
        assert verified.completed_at >= verification_started
        summary = await analytics_summary(session, order.event_id)
        assert summary["completed_orders"] == 1
        assert summary["auto_closed_orders"] == 0
        assert summary["average_fulfillment_minutes"] is not None


async def test_existing_order_without_deadline_is_scheduled_from_now(order_db):
    factory, order_id = order_db
    async with factory() as session:
        order = await session.get(Order, order_id)
        order.next_transition_at = None
        await session.commit()
    now = utc_now()
    assert await process_due_orders(factory, None, now=now) == 0
    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order.next_transition_at is not None
        assert order.next_transition_at.replace(tzinfo=now.tzinfo) > now


async def test_simultaneous_sqlite_status_changes_reject_stale_version(order_db):
    factory, order_id = order_db
    async with factory() as first, factory() as second:
        outcomes = await asyncio.gather(
            transition_order(
                first, order_id, OrderStatus.ACCEPTED, actor="bartender", expected_version=1
            ),
            transition_order(
                second, order_id, OrderStatus.ACCEPTED, actor="bartender", expected_version=1
            ),
            return_exceptions=True,
        )
    assert sum(isinstance(result, Order) for result in outcomes) == 1
    assert sum(isinstance(result, ConflictError) for result in outcomes) == 1
