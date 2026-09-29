"""Estimated stage durations based on the queue at stage entry."""

from __future__ import annotations

from datetime import timedelta

from app.domain import OrderStatus

STAGE_SECONDS = {
    OrderStatus.SUBMITTED.value: (30, 180),
    OrderStatus.ACCEPTED.value: (30, 240),
    OrderStatus.PREPARING.value: (180, 900),
    OrderStatus.READY.value: (600, 1800),
}

NEXT_STATUS = {
    OrderStatus.SUBMITTED.value: OrderStatus.ACCEPTED,
    OrderStatus.ACCEPTED.value: OrderStatus.PREPARING,
    OrderStatus.PREPARING.value: OrderStatus.READY,
    OrderStatus.READY.value: OrderStatus.COMPLETED,
}


def stage_delay(status: str, queue_size: int, item_count: int = 1) -> timedelta:
    low, high = STAGE_SECONDS[status]
    load = min(max(queue_size - 1, 0), 24) / 24
    seconds = round(low + (high - low) * load)
    if status == OrderStatus.PREPARING.value:
        seconds += max(item_count - 1, 0) * 45
    return timedelta(seconds=seconds)
