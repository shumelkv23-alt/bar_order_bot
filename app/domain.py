from enum import StrEnum


class Language(StrEnum):
    RU = "ru"
    EN = "en"


class Role(StrEnum):
    GUEST = "guest"
    BARTENDER = "bartender"
    ADMIN = "admin"
    OWNER = "owner"


class EventStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"


class OrderStatus(StrEnum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PREPARING = "preparing"
    READY = "ready"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class SpecialRequestStatus(StrEnum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"


ACTIVE_ORDER_STATUSES = {
    OrderStatus.SUBMITTED,
    OrderStatus.ACCEPTED,
    OrderStatus.PREPARING,
    OrderStatus.READY,
}


ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.SUBMITTED: {
        OrderStatus.ACCEPTED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
    },
    OrderStatus.ACCEPTED: {
        OrderStatus.PREPARING,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
    },
    OrderStatus.PREPARING: {
        OrderStatus.READY,
        OrderStatus.CANCELLED,
    },
    OrderStatus.READY: {OrderStatus.COMPLETED},
    OrderStatus.COMPLETED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
}


def ensure_transition(current: OrderStatus | str, target: OrderStatus | str) -> None:
    current_status = OrderStatus(current)
    target_status = OrderStatus(target)
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise ValueError(
            f"Transition {current_status.value} -> {target_status.value} is not allowed"
        )
