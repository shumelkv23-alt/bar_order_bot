"""Event-scoped guest achievements and the evening quiz."""

from __future__ import annotations

import hashlib
import hmac
from time import time

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventAchievement

ACHIEVEMENTS = (
    (
        "first_contact", "Первый контакт", "First contact",
        "Первый заказ вечера", "First order of the evening", "✦",
    ),
    (
        "pathfinder", "Следопыт", "Pathfinder",
        "Найти спрятанный знак", "Find the hidden mark", "✧",
    ),
    (
        "connoisseur", "Знаток", "Connoisseur",
        "Ответить на вопрос викторины", "Answer the quiz question", "◆",
    ),
)


def discovery_token(event_id: int, user_id: int, item_id: int, secret: str) -> str:
    issued_at = int(time())
    message = f"{event_id}:{user_id}:{item_id}:{issued_at}"
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{item_id}:{issued_at}:{signature}"


def valid_discovery_token(token: str, event_id: int, user_id: int, secret: str) -> bool:
    try:
        item_id, issued_at, signature = token.split(":", 2)
        issued = int(issued_at)
        int(item_id)
    except (ValueError, TypeError):
        return False
    if not secret or not 0 <= time() - issued <= 300:
        return False
    message = f"{event_id}:{user_id}:{item_id}:{issued}"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


async def award_achievement(
    session: AsyncSession, event_id: int, user_id: int, code: str
) -> bool:
    """Insert once, including when two guest tabs act at the same time.

    The caller owns the transaction, allowing order submission and its badge to
    commit together.
    """
    if code not in {entry[0] for entry in ACHIEVEMENTS}:
        raise ValueError("Unknown achievement")
    dialect = session.get_bind().dialect.name
    values = {"event_id": event_id, "user_id": user_id, "code": code}
    if dialect == "postgresql":
        statement = pg_insert(EventAchievement).values(**values)
    elif dialect == "sqlite":
        statement = sqlite_insert(EventAchievement).values(**values)
    else:
        raise RuntimeError(f"Unsupported database dialect: {dialect}")
    statement = statement.on_conflict_do_nothing(
        index_elements=["event_id", "user_id", "code"]
    ).returning(EventAchievement.id)
    return (await session.scalar(statement)) is not None


async def achievement_collection(
    session: AsyncSession, event_id: int, user_id: int, language: str
) -> dict:
    rows = (await session.scalars(
        select(EventAchievement).where(
            EventAchievement.event_id == event_id,
            EventAchievement.user_id == user_id,
        )
    )).all()
    earned = {row.code: row.awarded_at.isoformat() for row in rows}
    english = language == "en"
    return {
        "achievements": [
            {
                "code": code,
                "name": name_en if english else name_ru,
                "description": description_en if english else description_ru,
                "symbol": symbol,
                "awarded_at": earned.get(code),
            }
            for code, name_ru, name_en, description_ru, description_en, symbol in ACHIEVEMENTS
        ],
        "quiz": {
            "question": (
                "Which herb is used in a mojito?"
                if english else "Какая пряная трава входит в мохито?"
            ),
            "options": [
                {"id": "lime", "label": "Lime" if english else "Лайм"},
                {"id": "mint", "label": "Mint" if english else "Мята"},
                {"id": "soda", "label": "Soda" if english else "Содовая"},
            ],
        },
    }
