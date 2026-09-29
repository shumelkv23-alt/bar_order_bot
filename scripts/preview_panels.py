"""Isolated UI preview. No production DB, Telegram calls, or real credentials.

Run: python scripts/preview_panels.py
Open http://127.0.0.1:8766/staff with key preview-admin.
The temporary SQLite database is removed when the preview stops.
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def seed_preview() -> None:
    from app.db import async_session_factory, init_db
    from app.domain import OrderStatus
    from app.services.orders import (
        add_to_cart,
        create_special_request,
        get_active_event,
        list_event_menu,
        submit_cart,
        transition_order,
        upsert_user,
    )
    from app.services.seed import seed_demo_data

    await init_db()
    async with async_session_factory() as session:
        await seed_demo_data(session)
        event = await get_active_event(session)
        event.name = "Azati · Командный вечер"
        menu = await list_event_menu(session, event.id)
        names = ["Александр", "Мария", "Денис", "Анна", "Михаил", "Виктория"]
        for index in range(22):
            user = await upsert_user(session, 990000 + index, names[index % len(names)])
            await add_to_cart(session, user.id, event.id, menu[index % len(menu)].menu_item_id, 1)
            if index % 3 == 0:
                await add_to_cart(
                    session,
                    user.id,
                    event.id,
                    menu[(index + 3) % len(menu)].menu_item_id,
                    2,
                )
            order = await submit_cart(
                session, user.id, event.id, "Без льда, пожалуйста" if index == 0 else ""
            )
            steps = index % 4 if index < 8 else 4
            transitions = (
                OrderStatus.ACCEPTED,
                OrderStatus.PREPARING,
                OrderStatus.READY,
                OrderStatus.COMPLETED,
            )
            for target in transitions[:steps]:
                order = await transition_order(
                    session,
                    order.id,
                    target,
                    actor="preview",
                    expected_version=order.version,
                )
            age = [12, 7, 4, 2][index % 4] if index < 8 else (index - 7) * 22
            order.created_at = datetime.now(UTC) - timedelta(minutes=age)
            if steps == 4:
                order.completed_at = order.created_at + timedelta(minutes=4 + index % 6)
            await session.commit()
        await create_special_request(
            session,
            user.id,
            event.id,
            "Что-нибудь кислое на джине, без сахара",
            source_transcript="Можно что-нибудь кисленькое, только не сладкое?",
        )


if __name__ == "__main__":
    with TemporaryDirectory(prefix="azati-panel-preview-") as folder:
        os.environ.update({
            "DATABASE_URL": "sqlite+aiosqlite:///" + str(Path(folder) / "preview.db"),
            "BOT_TOKEN": "", "WEBHOOK_BASE_URL": "", "SEED_DEMO": "false",
            "STAFF_TOKEN": "preview-staff", "ADMIN_TOKEN": "preview-admin",
            "OWNER_TOKEN": "preview-owner", "ANALYST_TOKEN": "preview-analyst",
        })
        asyncio.run(seed_preview())
        uvicorn.run("app.main:app", host="127.0.0.1", port=8766, log_level="warning")
