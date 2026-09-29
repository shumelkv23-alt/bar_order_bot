"""Event-scoped, consent-aware guest leaderboard."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import EventLeaderboardPreference, Order, OrderItem, User


def guest_alias(event_id: int, user_id: int) -> str:
    settings = get_settings()
    secret = settings.leaderboard_pseudonym_secret
    if not secret:
        if settings.environment not in {"development", "test"}:
            raise RuntimeError("LEADERBOARD_PSEUDONYM_SECRET is required")
        secret = "development-only-leaderboard-secret"
    digest = hmac.new(secret.encode(), f"{event_id}:{user_id}".encode(), hashlib.sha256).digest()
    return "Гость #" + base64.b32encode(digest).decode("ascii")[:6]


async def leaderboard(
    session: AsyncSession, event_id: int, own_user_id: int, language: str
) -> dict:
    counted_statuses = ["accepted", "preparing", "ready", "completed"]
    statement = (
        select(
            User.id,
            User.display_name,
            func.sum(OrderItem.quantity).label("score"),
            func.min(Order.created_at).label("first_order"),
            EventLeaderboardPreference.show_telegram_name,
        )
        .join(Order, Order.user_id == User.id)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .outerjoin(
            EventLeaderboardPreference,
            (EventLeaderboardPreference.user_id == User.id)
            & (EventLeaderboardPreference.event_id == event_id),
        )
        .where(Order.event_id == event_id, Order.status.in_(counted_statuses))
        .group_by(User.id, User.display_name, EventLeaderboardPreference.show_telegram_name)
        .order_by(func.sum(OrderItem.quantity).desc(), func.min(Order.created_at), User.id)
    )
    rows = (await session.execute(statement)).all()
    own_preference = await session.scalar(
        select(EventLeaderboardPreference).where(
            EventLeaderboardPreference.event_id == event_id,
            EventLeaderboardPreference.user_id == own_user_id,
        )
    )

    def view(row: tuple, position: int) -> dict:
        user_id, display_name, score, _, consent = row
        alias = guest_alias(event_id, user_id)
        if language == "en":
            alias = alias.replace("Гость", "Guest")
        return {
            "rank": position,
            "name": display_name if consent else alias,
            "score": int(score),
            "is_me": user_id == own_user_id,
        }

    top = [view(row, position) for position, row in enumerate(rows[:10], 1)]
    mine = next(
        (view(row, position) for position, row in enumerate(rows, 1) if row[0] == own_user_id),
        None,
    )
    return {
        "top": top,
        "me": mine,
        "show_telegram_name": bool(own_preference and own_preference.show_telegram_name),
        "updated_at": datetime.now(UTC).isoformat(),
    }


async def set_name_consent(
    session: AsyncSession, event_id: int, user_id: int, show_telegram_name: bool
) -> bool:
    preference = await session.scalar(
        select(EventLeaderboardPreference).where(
            EventLeaderboardPreference.event_id == event_id,
            EventLeaderboardPreference.user_id == user_id,
        )
    )
    if preference is None:
        preference = EventLeaderboardPreference(
            event_id=event_id,
            user_id=user_id,
            show_telegram_name=show_telegram_name,
        )
        session.add(preference)
    else:
        preference.show_telegram_name = show_telegram_name
    await session.commit()
    return preference.show_telegram_name
