"""Secret menu riddles, unlocks, and portion accounting."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SecretOffer, SecretUnlock


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalized_answer(answer: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", answer).casefold()).strip()


def set_answer(offer: SecretOffer, answer: str) -> None:
    normalized = normalized_answer(answer)
    if not normalized:
        raise ValueError("Answer cannot be empty")
    offer.answer_salt = secrets.token_hex(16)
    offer.answer_hash = hashlib.pbkdf2_hmac(
        "sha256", normalized.encode(), bytes.fromhex(offer.answer_salt), 100_000
    ).hex()


def answer_matches(offer: SecretOffer, answer: str) -> bool:
    normalized = normalized_answer(answer)
    if not normalized:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", normalized.encode(), bytes.fromhex(offer.answer_salt), 100_000
    ).hex()
    return hmac.compare_digest(candidate, offer.answer_hash)


def offer_available(offer: SecretOffer, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return (
        offer.is_active
        and as_utc(offer.available_from) <= now < as_utc(offer.available_until)
        and offer.portions_used < offer.portions_total
        and not offer.menu_item.is_archived
        and offer.menu_item.category.is_active
    )


async def secret_offer_for_item(
    session: AsyncSession, event_id: int, menu_item_id: int
) -> SecretOffer | None:
    return await session.scalar(
        select(SecretOffer).where(
            SecretOffer.event_id == event_id,
            SecretOffer.menu_item_id == menu_item_id,
        )
    )


async def user_has_unlock(session: AsyncSession, offer_id: int, user_id: int) -> bool:
    return await session.scalar(
        select(SecretUnlock.id).where(
            SecretUnlock.offer_id == offer_id, SecretUnlock.user_id == user_id
        )
    ) is not None


async def unlock_offer(session: AsyncSession, offer_id: int, user_id: int) -> bool:
    dialect = session.get_bind().dialect.name
    values = {"offer_id": offer_id, "user_id": user_id}
    if dialect == "postgresql":
        statement = pg_insert(SecretUnlock).values(**values)
    elif dialect == "sqlite":
        statement = sqlite_insert(SecretUnlock).values(**values)
    else:
        raise RuntimeError(f"Unsupported database dialect: {dialect}")
    statement = statement.on_conflict_do_nothing(
        index_elements=["offer_id", "user_id"]
    ).returning(SecretUnlock.id)
    return (await session.scalar(statement)) is not None


async def reserve_portions(session: AsyncSession, offer_id: int, quantity: int) -> bool:
    now = datetime.now(UTC)
    statement = (
        update(SecretOffer)
        .where(
            SecretOffer.id == offer_id,
            SecretOffer.is_active.is_(True),
            SecretOffer.available_from <= now,
            SecretOffer.available_until > now,
            SecretOffer.portions_used + quantity <= SecretOffer.portions_total,
        )
        .values(portions_used=SecretOffer.portions_used + quantity)
        .returning(SecretOffer.id)
        .execution_options(synchronize_session=False)
    )
    return (await session.scalar(statement)) is not None


async def release_portions(session: AsyncSession, offer_id: int, quantity: int) -> None:
    result = await session.scalar(
        update(SecretOffer)
        .where(SecretOffer.id == offer_id, SecretOffer.portions_used >= quantity)
        .values(portions_used=SecretOffer.portions_used - quantity)
        .returning(SecretOffer.id)
        .execution_options(synchronize_session=False)
    )
    if result is None:
        raise RuntimeError("Secret offer stock ledger is inconsistent")
