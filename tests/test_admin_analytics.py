from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.admin import (
    activate_event,
    create_category,
    create_event,
    create_menu_item,
    create_modifier,
    get_catalog,
)
from app.db import Base
from app.domain import OrderStatus, SpecialRequestStatus
from app.schemas import CategoryCreate, EventCreate, MenuItemCreate, ModifierCreate
from app.services.orders import (
    add_to_cart,
    analytics_summary,
    analytics_to_csv,
    create_special_request,
    get_active_event,
    list_event_menu,
    list_special_requests,
    submit_cart,
    transition_order,
    transition_special_request,
    upsert_user,
)
from app.services.seed import MENU_ITEMS, seed_demo_data


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


async def test_admin_can_build_catalog_and_activate_event(session) -> None:
    category = await create_category(
        CategoryCreate(name_ru="Шоты", name_en="Shots", sort_order=40),
        session,
        "admin",
    )
    modifier = await create_modifier(
        ModifierCreate(name_ru="С лаймом", name_en="With lime", aliases=["лайм"]),
        session,
        "admin",
    )
    item = await create_menu_item(
        MenuItemCreate(
            category_id=category["id"],
            name_ru="Тестовый шот",
            name_en="Test shot",
            modifier_ids=[modifier["id"]],
        ),
        session,
        "admin",
    )
    assert item["modifier_ids"] == [modifier["id"]]
    assert item["events"] and item["events"][0]["is_available"] is True

    now = datetime.now(UTC)
    event = await create_event(
        EventCreate(
            code="next_event",
            name="Next Event",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, hours=6),
        ),
        session,
        "admin",
    )
    assert event["menu_items_count"] == len(MENU_ITEMS) + 1

    activated = await activate_event(event["id"], session, "admin")
    assert activated["status"] == "active"
    assert (await get_active_event(session)).id == event["id"]

    catalog = await get_catalog(session, "admin")
    assert any(row["name_ru"] == "Тестовый шот" for row in catalog["items"])


async def test_analytics_summary_and_csv_export(session) -> None:
    user = await upsert_user(session, 909, "Analytics Guest")
    event = await get_active_event(session)
    menu = await list_event_menu(session, event.id)
    await add_to_cart(session, user.id, event.id, menu[0].menu_item_id, 2)
    order = await submit_cart(session, user.id, event.id)
    for target in (
        OrderStatus.ACCEPTED,
        OrderStatus.PREPARING,
        OrderStatus.READY,
        OrderStatus.COMPLETED,
    ):
        order = await transition_order(
            session,
            order.id,
            target,
            actor="test",
            expected_version=order.version,
        )

    summary = await analytics_summary(session, event.id)
    assert summary["total_orders"] == 1
    assert summary["total_items"] == 2
    assert summary["unique_guests"] == 1
    assert summary["completed_orders"] == 1
    assert summary["completion_rate"] == 100.0
    assert summary["average_items_per_order"] == 2.0
    assert summary["average_fulfillment_minutes"] is not None
    assert summary["orders_by_hour"][0]["orders"] == 1

    exported = analytics_to_csv(summary)
    assert "summary,total_orders,1" in exported
    assert "status,completed,1" in exported
    assert "popular_item," in exported


async def test_bartender_can_process_special_voice_request(session) -> None:
    user = await upsert_user(session, 910, "Voice Guest")
    event = await get_active_event(session)
    request = await create_special_request(
        session,
        user.id,
        event.id,
        "маргарита без соли",
        2,
        source_transcript="Две маргариты без соли",
    )

    pending = await list_special_requests(
        session,
        event.id,
        statuses=[SpecialRequestStatus.SUBMITTED.value],
    )
    assert [row.id for row in pending] == [request.id]

    accepted = await transition_special_request(
        session,
        request.id,
        SpecialRequestStatus.ACCEPTED,
        actor="bartender:test",
        note="Сделаем на текиле",
    )
    assert accepted.status == SpecialRequestStatus.ACCEPTED.value
    assert accepted.bartender_note == "Сделаем на текиле"

    fulfilled = await transition_special_request(
        session,
        request.id,
        SpecialRequestStatus.FULFILLED,
        actor="bartender:test",
    )
    assert fulfilled.status == SpecialRequestStatus.FULFILLED.value
