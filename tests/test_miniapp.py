import hashlib
import hmac
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import miniapp
from app.api.admin import create_secret_offer, update_secret_offer
from app.db import Base, get_session
from app.domain import OrderStatus
from app.main import app
from app.models import MenuItem, User
from app.schemas import SecretOfferCreate, SecretOfferUpdate
from app.services import miniapp_leaderboard
from app.services.miniapp_auth import InvalidInitData, verify_init_data
from app.services.orders import (
    get_active_event,
    list_staff_orders,
    reopen_order_for_edit,
    transition_order,
)
from app.services.seed import seed_demo_data

BOT_TOKEN = "123456:test-token"


def signed_data(user_id: int = 101, *, auth_date: datetime | None = None) -> str:
    fields = {
        "auth_date": str(int((auth_date or datetime.now(UTC)).timestamp())),
        "user": json.dumps(
            {"id": user_id, "first_name": "Test", "last_name": "Guest", "language_code": "en"},
            separators=(",", ":"),
        ),
    }
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_telegram_init_data_rejects_tampering_and_expiry():
    guest = verify_init_data(signed_data(), BOT_TOKEN)
    assert guest.telegram_id == 101
    assert guest.display_name == "Test Guest"
    with pytest.raises(InvalidInitData):
        verify_init_data(signed_data().replace("101", "102"), BOT_TOKEN)
    with pytest.raises(InvalidInitData):
        verify_init_data(signed_data() + "&user=duplicate", BOT_TOKEN)
    with pytest.raises(InvalidInitData):
        verify_init_data(signed_data(auth_date=datetime.now(UTC) - timedelta(days=2)), BOT_TOKEN)


def test_miniapp_migration_upgrades_existing_schema(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20260927_0002_miniapp.py"
    spec = importlib.util.spec_from_file_location("miniapp_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE events (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, event_id INTEGER, user_id INTEGER)"
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()
        schema = inspect(connection)
        assert "idempotency_key" in {column["name"] for column in schema.get_columns("orders")}
        assert "event_leaderboard_preferences" in schema.get_table_names()
        assert "uq_orders_event_user_idempotency" in {
            index["name"] for index in schema.get_indexes("orders")
        }
    engine.dispose()


def test_achievements_migration_upgrades_existing_schema(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20260929_0003_achievements.py"
    spec = importlib.util.spec_from_file_location("achievements_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE events (id INTEGER PRIMARY KEY)")
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()
        migration.upgrade()
        schema = inspect(connection)
        assert "event_achievements" in schema.get_table_names()
        assert {"event_id", "user_id", "code", "awarded_at"}.issubset(
            {column["name"] for column in schema.get_columns("event_achievements")}
        )
    engine.dispose()


def test_secret_menu_migration_upgrades_existing_schema(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20260929_0004_secret_menu.py"
    spec = importlib.util.spec_from_file_location("secret_menu_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        for table in ("users", "events", "menu_items", "order_items"):
            connection.exec_driver_sql(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()
        migration.upgrade()
        schema = inspect(connection)
        assert {"secret_offers", "secret_unlocks"}.issubset(schema.get_table_names())
        assert "secret_offer_id" in {
            column["name"] for column in schema.get_columns("order_items")
        }
    engine.dispose()


def test_secret_offer_requires_timezone():
    with pytest.raises(ValueError, match="timezone"):
        SecretOfferCreate(
            event_id=1, menu_item_id=1,
            riddle_ru="Загадка", riddle_en="Riddle", answer="Ответ",
            available_from=datetime(2026, 9, 29, 18),
            available_until=datetime(2026, 9, 29, 19, tzinfo=UTC),
            portions_total=1,
        )
    with pytest.raises(ValueError, match="blank"):
        SecretOfferUpdate(answer="   ")


@pytest.fixture
async def miniapp_client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_demo_data(session)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.state.bot = None
    monkeypatch.setattr(miniapp, "get_settings", lambda: SimpleNamespace(bot_token=BOT_TOKEN))
    monkeypatch.setattr(
        miniapp_leaderboard,
        "get_settings",
        lambda: SimpleNamespace(leaderboard_pseudonym_secret="test-secret", environment="test"),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_miniapp_order_mystery_and_consent(miniapp_client):
    client, factory = miniapp_client
    headers = {"X-Telegram-Init-Data": signed_data()}
    bootstrap = (await client.get("/api/v1/miniapp/bootstrap", headers=headers)).json()
    assert bootstrap["event"]
    menu_response = await client.get("/api/v1/miniapp/menu", headers=headers)
    assert menu_response.headers["cache-control"] == "no-store"
    menu = menu_response.json()
    item = menu["categories"][0]["items"][0]
    assert "ingredients" in item
    assert "price" not in item

    mystery = await client.post("/api/v1/miniapp/mystery/generate", headers=headers, json={})
    assert mystery.status_code == 200
    assert mystery.json()["item"]["id"] in {
        product["id"] for category in menu["categories"] for product in category["items"]
    }

    added = await client.post(
        "/api/v1/miniapp/cart/items",
        headers=headers,
        json={"menu_item_id": item["id"], "quantity": 1},
    )
    assert added.status_code == 200
    assert added.json()["total_quantity"] == 1

    key = str(uuid4())
    payload = {"comment": "No ice", "idempotency_key": key}
    first = await client.post("/api/v1/miniapp/orders", headers=headers, json=payload)
    second = await client.post("/api/v1/miniapp/orders", headers=headers, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert "telegram_id" not in first.json()
    async with factory() as session:
        event = await get_active_event(session)
        queue = await list_staff_orders(session, event.id)
        assert len(queue) == 1
        await transition_order(session, queue[0].id, OrderStatus.ACCEPTED, actor="bartender")

    board = (await client.get("/api/v1/miniapp/leaderboard", headers=headers)).json()
    assert board["top"][0]["score"] == 1
    assert board["top"][0]["name"].startswith("Guest #")
    agreed = await client.put(
        "/api/v1/miniapp/leaderboard/privacy",
        headers=headers,
        json={"show_telegram_name": True},
    )
    assert agreed.status_code == 200
    assert (await client.get("/api/v1/miniapp/leaderboard", headers=headers)).json()["top"][0][
        "name"
    ] == "Test Guest"
    await client.put(
        "/api/v1/miniapp/leaderboard/privacy",
        headers=headers,
        json={"show_telegram_name": False},
    )
    assert (
        (await client.get("/api/v1/miniapp/leaderboard", headers=headers))
        .json()["top"][0]["name"]
        .startswith("Guest #")
    )
    async with factory() as session:
        event = await get_active_event(session)
        event.status = "closed"
        await session.commit()
    late_retry = await client.post("/api/v1/miniapp/orders", headers=headers, json=payload)
    assert late_retry.status_code == 200
    assert late_retry.json()["id"] == first.json()["id"]


async def test_achievements_are_awarded_once_and_kept_in_profile(miniapp_client):
    client, _ = miniapp_client
    headers = {"X-Telegram-Init-Data": signed_data(303)}
    profile_url = "/api/v1/miniapp/achievements"
    first_profile = (await client.get(profile_url, headers=headers)).json()
    assert {badge["code"] for badge in first_profile["achievements"]} == {
        "first_contact", "pathfinder", "connoisseur"
    }
    assert all(badge["awarded_at"] is None for badge in first_profile["achievements"])

    wrong = await client.post(f"{profile_url}/quiz", headers=headers, json={"answer": "lime"})
    assert wrong.json() == {"correct": False, "new_achievement": None}
    correct = await client.post(f"{profile_url}/quiz", headers=headers, json={"answer": "mint"})
    assert correct.json() == {"correct": True, "new_achievement": "connoisseur"}
    repeated = await client.post(f"{profile_url}/quiz", headers=headers, json={"answer": "mint"})
    assert repeated.json()["new_achievement"] is None

    mystery = await client.post("/api/v1/miniapp/mystery/generate", headers=headers, json={})
    token = mystery.json()["discovery_token"]
    assert (await client.post(
        f"{profile_url}/discover", headers=headers, json={"token": "forged"}
    )).status_code == 422
    assert (await client.post(
        f"{profile_url}/discover",
        headers={"X-Telegram-Init-Data": signed_data(404)}, json={"token": token}
    )).status_code == 422
    discovered = await client.post(
        f"{profile_url}/discover", headers=headers, json={"token": token}
    )
    assert discovered.json()["new_achievement"] == "pathfinder"
    assert (await client.post(
        f"{profile_url}/discover", headers=headers, json={"token": token}
    )).json() == {
        "new_achievement": None
    }

    menu = (await client.get("/api/v1/miniapp/menu", headers=headers)).json()
    item_id = menu["categories"][0]["items"][0]["id"]
    await client.post(
        "/api/v1/miniapp/cart/items", headers=headers,
        json={"menu_item_id": item_id, "quantity": 1},
    )
    payload = {"comment": "", "idempotency_key": str(uuid4())}
    order = await client.post("/api/v1/miniapp/orders", headers=headers, json=payload)
    assert order.status_code == 200
    assert order.json()["new_achievements"] == ["first_contact"]
    retry = await client.post("/api/v1/miniapp/orders", headers=headers, json=payload)
    assert retry.json()["new_achievements"] == []

    final_profile = (await client.get(profile_url, headers=headers)).json()
    assert all(badge["awarded_at"] for badge in final_profile["achievements"])
    other_profile = (await client.get(
        profile_url, headers={"X-Telegram-Init-Data": signed_data(404)}
    )).json()
    assert all(badge["awarded_at"] is None for badge in other_profile["achievements"])


async def test_secret_menu_unlock_window_stock_and_cancellation(miniapp_client):
    client, factory = miniapp_client
    first_headers = {"X-Telegram-Init-Data": signed_data(501)}
    second_headers = {"X-Telegram-Init-Data": signed_data(502)}
    catalog = (await client.get("/api/v1/miniapp/menu", headers=first_headers)).json()
    item_id = catalog["categories"][0]["items"][0]["id"]
    now = datetime.now(UTC)
    async with factory() as session:
        event = await get_active_event(session)
        offer = await create_secret_offer(
            SecretOfferCreate(
                event_id=event.id, menu_item_id=item_id,
                riddle_ru="Что растёт в саду?", riddle_en="What grows in a garden?",
                answer="Мята", available_from=now + timedelta(minutes=10),
                available_until=now + timedelta(hours=1), portions_total=1,
            ), session, "admin",
        )
    offer_id = offer["id"]
    assert offer["available_from"].endswith("+00:00")
    secret_url = "/api/v1/miniapp/secret-menu"
    menu_after = (await client.get("/api/v1/miniapp/menu", headers=first_headers)).json()
    assert item_id not in {
        item["id"] for category in menu_after["categories"] for item in category["items"]
    }
    assert (await client.post(
        "/api/v1/miniapp/cart/items", headers=first_headers,
        json={"menu_item_id": item_id, "quantity": 1},
    )).status_code == 409
    upcoming = (await client.get(secret_url, headers=first_headers)).json()["offers"][0]
    assert upcoming["item"] is None and upcoming["available"] is False
    assert upcoming["available_from"].endswith("+00:00")
    assert (await client.post(
        f"{secret_url}/{offer_id}/unlock", headers=first_headers, json={"answer": "Мята"}
    )).status_code == 409

    async with factory() as session:
        await update_secret_offer(
            offer_id, SecretOfferUpdate(available_from=now - timedelta(minutes=1)),
            session, "admin",
        )
    assert (await client.post(
        f"{secret_url}/{offer_id}/unlock", headers=first_headers, json={"answer": "укроп"}
    )).status_code == 422
    revealed = (await client.post(
        f"{secret_url}/{offer_id}/unlock", headers=first_headers,
        json={"answer": "  МЯТА  "},
    )).json()
    assert revealed["item"]["id"] == item_id
    second_offer = (await client.get(secret_url, headers=second_headers)).json()["offers"][0]
    assert second_offer["item"] is None
    second_unlock = await client.post(
        f"{secret_url}/{offer_id}/unlock", headers=second_headers, json={"answer": "мята"}
    )
    assert second_unlock.status_code == 200
    second_added = await client.post(
        "/api/v1/miniapp/cart/items", headers=second_headers,
        json={"menu_item_id": item_id, "quantity": 1},
    )
    assert second_added.status_code == 200

    added = await client.post(
        "/api/v1/miniapp/cart/items", headers=first_headers,
        json={"menu_item_id": item_id, "quantity": 1},
    )
    assert added.status_code == 200
    ordered = await client.post(
        "/api/v1/miniapp/orders", headers=first_headers,
        json={"comment": "", "idempotency_key": str(uuid4())},
    )
    assert ordered.status_code == 200
    after_order = (await client.get(secret_url, headers=first_headers)).json()["offers"][0]
    assert after_order["remaining"] == 0
    second_payload = {"comment": "", "idempotency_key": str(uuid4())}
    sold_out_order = await client.post(
        "/api/v1/miniapp/orders", headers=second_headers, json=second_payload
    )
    assert sold_out_order.status_code == 409
    async with factory() as session:
        await transition_order(session, ordered.json()["id"], OrderStatus.CANCELLED, actor="test")
    after_cancel = (await client.get(secret_url, headers=first_headers)).json()["offers"][0]
    assert after_cancel["remaining"] == 1
    second_order = await client.post(
        "/api/v1/miniapp/orders", headers=second_headers, json=second_payload
    )
    assert second_order.status_code == 200
    after_second = (await client.get(secret_url, headers=first_headers)).json()["offers"][0]
    assert after_second["remaining"] == 0
    async with factory() as session:
        _, restored_cart = await reopen_order_for_edit(
            session, second_order.json()["id"],
            (await session.scalar(select(User.id).where(User.telegram_id == 502))),
            expected_version=second_order.json()["version"], actor="test",
        )
        assert restored_cart.items[0].menu_item_id == item_id
    after_edit = (await client.get(secret_url, headers=first_headers)).json()["offers"][0]
    assert after_edit["remaining"] == 1
    async with factory() as session:
        event = await get_active_event(session)
        event.orders_enabled = False
        await session.commit()
    paused = (await client.get(secret_url, headers=first_headers)).json()["offers"][0]
    assert paused["available"] is False
    assert paused["unavailable_reason"] == "unavailable"


async def test_secret_offer_rejects_inactive_category(miniapp_client):
    client, factory = miniapp_client
    headers = {"X-Telegram-Init-Data": signed_data(601)}
    menu = (await client.get("/api/v1/miniapp/menu", headers=headers)).json()
    item_id = menu["categories"][0]["items"][0]["id"]
    async with factory() as session:
        event = await get_active_event(session)
        item = await session.get(MenuItem, item_id)
        item.category.is_active = False
        await session.commit()
        now = datetime.now(UTC)
        with pytest.raises(HTTPException) as error:
            await create_secret_offer(
                SecretOfferCreate(
                    event_id=event.id, menu_item_id=item_id,
                    riddle_ru="Загадка", riddle_en="Riddle", answer="Ответ",
                    available_from=now, available_until=now + timedelta(hours=1),
                    portions_total=1,
                ), session, "admin",
            )
        assert error.value.status_code == 404


async def test_miniapp_requires_auth_and_isolates_carts(miniapp_client):
    client, _ = miniapp_client
    assert (await client.get("/api/v1/miniapp/bootstrap")).status_code == 401
    headers_one = {"X-Telegram-Init-Data": signed_data(101)}
    headers_two = {"X-Telegram-Init-Data": signed_data(202)}
    menu = (await client.get("/api/v1/miniapp/menu", headers=headers_one)).json()
    item_id = menu["categories"][0]["items"][0]["id"]
    created = await client.post(
        "/api/v1/miniapp/cart/items",
        headers=headers_one,
        json={"menu_item_id": item_id, "quantity": 1},
    )
    line_id = created.json()["items"][0]["id"]
    assert (
        await client.delete(f"/api/v1/miniapp/cart/items/{line_id}", headers=headers_two)
    ).status_code == 404
    assert (await client.get("/api/v1/miniapp/cart", headers=headers_two)).json()["items"] == []
