import logging
from typing import Annotated

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.domain import Language, OrderStatus, SpecialRequestStatus
from app.models import Event
from app.schemas import OrderStatusUpdate, SpecialRequestStatusUpdate
from app.services.orders import (
    DomainError,
    analytics_summary,
    analytics_to_csv,
    get_active_event,
    list_special_requests,
    list_staff_orders,
    order_to_dict,
    special_request_to_dict,
    transition_order,
    transition_special_request,
)

from .dependencies import require_analytics, require_panel_role, require_staff
from .errors import domain_http_error

router = APIRouter(prefix="/api/v1", tags=["staff"])
Session = Annotated[AsyncSession, Depends(get_session)]
logger = logging.getLogger(__name__)


@router.get("/session")
async def panel_session(role: Annotated[str, Depends(require_panel_role)]) -> dict:
    permissions = {
        "admin": ["staff", "admin", "analytics"],
        "bartender": ["staff"],
        "owner": ["analytics"],
        "analyst": ["analytics"],
    }
    return {"role": role, "permissions": permissions[role]}


STATUS_MESSAGES = {
    Language.RU: {
        OrderStatus.ACCEPTED: "Заказ {number} принят барменом.",
        OrderStatus.PREPARING: "Заказ {number} готовится.",
        OrderStatus.READY: "Заказ {number} готов. Покажите этот номер у барной стойки.",
        OrderStatus.COMPLETED: "Заказ {number} выдан. Спасибо!",
        OrderStatus.CANCELLED: "Заказ {number} отменён.",
        OrderStatus.REJECTED: "Заказ {number} отклонён сотрудником.",
    },
    Language.EN: {
        OrderStatus.ACCEPTED: "Order {number} has been accepted by the bartender.",
        OrderStatus.PREPARING: "Order {number} is being prepared.",
        OrderStatus.READY: "Order {number} is ready. Show this number at the bar.",
        OrderStatus.COMPLETED: "Order {number} has been collected. Thank you!",
        OrderStatus.CANCELLED: "Order {number} has been cancelled.",
        OrderStatus.REJECTED: "Order {number} has been declined by the staff.",
    },
}

SPECIAL_REQUEST_MESSAGES = {
    Language.RU: {
        SpecialRequestStatus.ACCEPTED: "Бармен принял особый запрос: {request}.",
        SpecialRequestStatus.FULFILLED: "Особый запрос готов: {request}.",
        SpecialRequestStatus.REJECTED: "Особый запрос отклонён: {request}.{note}",
    },
    Language.EN: {
        SpecialRequestStatus.ACCEPTED: "The bartender accepted your special request: {request}.",
        SpecialRequestStatus.FULFILLED: "Your special request is ready: {request}.",
        SpecialRequestStatus.REJECTED: "Your special request was declined: {request}.{note}",
    },
}


@router.get("/staff/orders")
async def staff_orders(
    session: Session,
    _role: Annotated[str, Depends(require_staff)],
    event_code: str | None = None,
) -> dict:
    try:
        event = await get_active_event(session, event_code)
        orders = await list_staff_orders(
            session,
            event.id,
            statuses=[
                OrderStatus.SUBMITTED.value,
                OrderStatus.ACCEPTED.value,
                OrderStatus.PREPARING.value,
                OrderStatus.READY.value,
            ],
        )
        special_requests = await list_special_requests(
            session,
            event.id,
            statuses=[
                SpecialRequestStatus.SUBMITTED.value,
                SpecialRequestStatus.ACCEPTED.value,
            ],
        )
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    return {
        "event": {"id": event.id, "name": event.name, "orders_enabled": event.orders_enabled},
        "configuration": {
            "warning_minutes": get_settings().staff_warning_minutes,
            "critical_minutes": get_settings().staff_critical_minutes,
            "poll_interval_ms": 2500,
        },
        "orders": [order_to_dict(order) for order in orders],
        "special_requests": [
            special_request_to_dict(request, request.user.language) for request in special_requests
        ],
    }


@router.patch("/staff/special-requests/{request_id}")
async def update_special_request(
    request_id: int,
    payload: SpecialRequestStatusUpdate,
    request: Request,
    session: Session,
    role: Annotated[str, Depends(require_staff)],
) -> dict:
    try:
        special = await transition_special_request(
            session,
            request_id,
            payload.status,
            actor=role,
            note=payload.note,
        )
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    bot: Bot | None = getattr(request.app.state, "bot", None)
    language = Language.EN if special.user.language == Language.EN.value else Language.RU
    template = SPECIAL_REQUEST_MESSAGES[language].get(payload.status)
    if bot and template:
        note = f" {payload.note}" if payload.note else ""
        try:
            await bot.send_message(
                special.user.telegram_id,
                template.format(request=special.request_text, note=note),
            )
        except Exception as exc:
            logger.warning("Telegram special-request notification failed: %s", exc)
    return special_request_to_dict(special, special.user.language)


@router.patch("/staff/orders/{order_id}/status")
async def update_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    request: Request,
    session: Session,
    role: Annotated[str, Depends(require_staff)],
) -> dict:
    try:
        order = await transition_order(
            session,
            order_id,
            payload.status,
            actor=role,
            expected_version=payload.expected_version,
            reason=payload.reason,
        )
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    bot: Bot | None = getattr(request.app.state, "bot", None)
    language = Language.EN if order.user.language == Language.EN.value else Language.RU
    message_template = STATUS_MESSAGES[language].get(payload.status)
    if bot and message_template:
        try:
            await bot.send_message(
                order.user.telegram_id,
                message_template.format(number=order.public_number),
            )
        except Exception as exc:
            # The status change must not roll back if Telegram is temporarily unavailable.
            logger.warning("Telegram status notification failed: %s", exc)
    return order_to_dict(order)


@router.get("/analytics/events")
async def analytics_events(
    session: Session,
    _role: Annotated[str, Depends(require_analytics)],
) -> dict:
    events = list(
        (
            await session.scalars(select(Event).order_by(Event.starts_at.desc(), Event.id.desc()))
        ).all()
    )
    return {
        "events": [
            {
                "id": event.id,
                "code": event.code,
                "name": event.name,
                "status": event.status,
                "starts_at": event.starts_at.isoformat(),
                "ends_at": event.ends_at.isoformat(),
            }
            for event in events
        ]
    }


@router.get("/analytics/events/{event_id}")
async def event_analytics(
    event_id: int,
    session: Session,
    _role: Annotated[str, Depends(require_analytics)],
) -> dict:
    if not await session.get(Event, event_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return await analytics_summary(session, event_id)


@router.get("/analytics/events/{event_id}/export.csv")
async def export_event_analytics(
    event_id: int,
    session: Session,
    _role: Annotated[str, Depends(require_analytics)],
) -> Response:
    event = await session.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    summary = await analytics_summary(session, event_id)
    return Response(
        content="\ufeff" + analytics_to_csv(summary),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="analytics-{event.code}.csv"'},
    )
