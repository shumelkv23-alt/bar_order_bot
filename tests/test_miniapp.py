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
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import miniapp
from app.db import Base, get_session
from app.domain import OrderStatus
from app.main import app
from app.services import miniapp_leaderboard
from app.services.miniapp_auth import InvalidInitData, verify_init_data
from app.services.orders import get_active_event, list_staff_orders, transition_order
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
