"""Move due orders through the queue using persisted stage deadlines."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.domain import ACTIVE_ORDER_STATUSES, EventStatus, Language, OrderStatus
from app.models import Event, Order, utc_now
from app.services.auto_progress_policy import NEXT_STATUS, stage_delay
from app.services.orders import DomainError, transition_order

logger = logging.getLogger(__name__)
ACTIVE = [status.value for status in ACTIVE_ORDER_STATUSES]
AUTO_COMPLETION_MESSAGES = {
    Language.RU: (
        "Заказ {number} закрыт по таймеру после готовности. "
        "Если вы его не получили, обратитесь к бармену."
    ),
    Language.EN: (
        "Order {number} was closed by the timer after it became ready. "
        "If you have not collected it, please ask the bartender."
    ),
}
AUTO_STAGE_MESSAGES = {
    Language.RU: {
        OrderStatus.ACCEPTED: "Заказ {number} автоматически переведён в очередь бармена.",
        OrderStatus.PREPARING: "Заказ {number} перешёл к расчётному этапу приготовления.",
    },
    Language.EN: {
        OrderStatus.ACCEPTED: "Order {number} was automatically added to the bartender queue.",
        OrderStatus.PREPARING: "Order {number} reached its estimated preparation stage.",
    },
}


async def process_due_orders(
    session_factory: async_sessionmaker,
    bot: Bot | None,
    *,
    now: datetime | None = None,
) -> int:
    """Schedule legacy rows, then advance each due active order one stage."""
    if not get_settings().auto_progress_enabled:
        return 0
    now = now or utc_now()
    async with session_factory() as session:
        unscheduled = list(
            (await session.scalars(
                select(Order)
                .join(Event)
                .where(
                    Event.status == EventStatus.ACTIVE.value,
                    Order.status.in_(ACTIVE),
                    Order.next_transition_at.is_(None),
                )
                .order_by(Order.id)
            )).all()
        )
        counts: dict[int, int] = {}
        for order in unscheduled:
            if order.event_id not in counts:
                counts[order.event_id] = int(
                    await session.scalar(
                        select(func.count(Order.id)).where(
                            Order.event_id == order.event_id, Order.status.in_(ACTIVE)
                        )
                    ) or 1
                )
            await session.execute(
                update(Order)
                .where(
                    Order.id == order.id,
                    Order.status == order.status,
                    Order.version == order.version,
                    Order.next_transition_at.is_(None),
                )
                .values(
                    auto_queue_size_snapshot=counts[order.event_id],
                    next_transition_at=now
                    + stage_delay(
                        order.status,
                        counts[order.event_id],
                        sum(item.quantity for item in order.items),
                    ),
                )
            )
        await session.commit()

        due_ids = list(
            (await session.scalars(
                select(Order.id)
                .join(Event)
                .where(
                    Event.status == EventStatus.ACTIVE.value,
                    Order.status.in_(list(NEXT_STATUS)),
                    Order.next_transition_at <= now,
                )
                .order_by(Order.next_transition_at, Order.id)
            )).all()
        )

    moved = 0
    for order_id in due_ids:
        async with session_factory() as session:
            order = await session.get(Order, order_id)
            if not order or order.status not in NEXT_STATUS or not order.next_transition_at:
                continue
            deadline = order.next_transition_at
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=now.tzinfo)
            if deadline > now or order.event.status != EventStatus.ACTIVE.value:
                continue
            target = NEXT_STATUS[order.status]
            expected_version = order.version
            await session.commit()
            try:
                updated = await transition_order(
                    session,
                    order_id,
                    target,
                    actor="system:auto",
                    expected_version=expected_version,
                    reason="stage deadline elapsed",
                )
            except DomainError:
                await session.rollback()
                continue
            moved += 1
            if bot:
                # Reuse the same replacement path as a manual staff transition.
                from app.api.staff import _notify_status

                language = (
                    Language.EN if updated.user.language == Language.EN.value else Language.RU
                )
                template = (
                    AUTO_COMPLETION_MESSAGES[language]
                    if target == OrderStatus.COMPLETED
                    else AUTO_STAGE_MESSAGES[language][target]
                )
                try:
                    await _notify_status(
                        session,
                        bot,
                        Order,
                        updated.id,
                        target.value,
                        template.format(number=updated.public_number),
                        expected_version=updated.version,
                    )
                except Exception:
                    logger.exception("Automatic status notification failed for order %s", order_id)
    return moved


async def auto_progress_loop(session_factory: async_sessionmaker, bot: Bot | None) -> None:
    while True:
        try:
            await process_due_orders(session_factory, bot)
        except Exception:
            logger.exception("Automatic order progress failed")
        await asyncio.sleep(get_settings().auto_progress_poll_seconds)
