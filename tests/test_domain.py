import pytest

from app.domain import OrderStatus, ensure_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderStatus.SUBMITTED, OrderStatus.ACCEPTED),
        (OrderStatus.SUBMITTED, OrderStatus.CANCELLED),
        (OrderStatus.ACCEPTED, OrderStatus.PREPARING),
        (OrderStatus.PREPARING, OrderStatus.READY),
        (OrderStatus.READY, OrderStatus.COMPLETED),
    ],
)
def test_allowed_order_transitions(current: OrderStatus, target: OrderStatus) -> None:
    ensure_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderStatus.SUBMITTED, OrderStatus.READY),
        (OrderStatus.READY, OrderStatus.CANCELLED),
        (OrderStatus.COMPLETED, OrderStatus.SUBMITTED),
    ],
)
def test_forbidden_order_transitions(current: OrderStatus, target: OrderStatus) -> None:
    with pytest.raises(ValueError):
        ensure_transition(current, target)
