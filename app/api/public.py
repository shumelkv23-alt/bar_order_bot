from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain import Language, OrderStatus
from app.models import User
from app.schemas import (
    CartItemCreate,
    CartItemUpdate,
    OrderCreate,
    OrderEditRequest,
    UserUpsert,
)
from app.services.orders import (
    ConflictError,
    DomainError,
    add_to_cart,
    cart_to_dict,
    get_active_event,
    get_cart,
    get_order,
    list_event_menu,
    order_to_dict,
    remove_cart_item,
    reopen_order_for_edit,
    submit_cart,
    transition_order,
    update_cart_item_quantity,
    upsert_user,
)
from app.services.recommendations import RecommendationCandidate, rank_recommendations

from .errors import domain_http_error

router = APIRouter(prefix="/api/v1", tags=["public"])
Session = Annotated[AsyncSession, Depends(get_session)]


class RecommendationRequest(BaseModel):
    text: str = Field(min_length=2, max_length=500)
    event_code: str | None = None
    language: Language = Language.RU


@router.get("/events/active")
async def active_event(session: Session, code: str | None = None) -> dict:
    try:
        event = await get_active_event(session, code)
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    return {
        "id": event.id,
        "code": event.code,
        "name": event.name,
        "starts_at": event.starts_at,
        "ends_at": event.ends_at,
        "orders_enabled": event.orders_enabled,
    }


@router.post("/users")
async def create_or_update_user(payload: UserUpsert, session: Session) -> dict:
    user = await upsert_user(
        session,
        telegram_id=payload.telegram_id,
        display_name=payload.display_name,
        username=payload.username,
        language=payload.language.value,
    )
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "language": user.language,
    }


@router.get("/menu")
async def menu(
    session: Session,
    event_code: str | None = None,
    language: Language = Language.RU,
) -> dict:
    try:
        event = await get_active_event(session, event_code)
        entries = await list_event_menu(session, event.id)
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    grouped: dict[int, dict] = {}
    for entry in entries:
        item = entry.menu_item
        category = item.category
        category_data = grouped.setdefault(
            category.id,
            {
                "id": category.id,
                "name": category.name_en if language == Language.EN else category.name_ru,
                "items": [],
            },
        )
        category_data["items"].append(
            {
                "id": item.id,
                "name": item.name_en if language == Language.EN else item.name_ru,
                "description": (
                    item.description_en if language == Language.EN else item.description_ru
                ),
                "is_alcoholic": item.is_alcoholic,
                "modifiers": [
                    {
                        "id": link.modifier.id,
                        "name": (
                            link.modifier.name_en
                            if language == Language.EN
                            else link.modifier.name_ru
                        ),
                        "kind": link.modifier.kind,
                    }
                    for link in item.allowed_modifiers
                ],
            }
        )
    return {"event": event.name, "categories": list(grouped.values())}


async def _user_by_telegram(session: AsyncSession, telegram_id: int) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user:
        from app.services.orders import NotFoundError

        raise NotFoundError("Сначала зарегистрируйте пользователя")
    return user


@router.get("/users/{telegram_id}/cart")
async def read_cart(
    telegram_id: int,
    session: Session,
    event_code: str | None = None,
) -> dict:
    try:
        user = await _user_by_telegram(session, telegram_id)
        event = await get_active_event(session, event_code)
        cart = await get_cart(session, user.id, event.id)
        return cart_to_dict(cart, user.language)
    except DomainError as exc:
        raise domain_http_error(exc) from exc


@router.post("/users/{telegram_id}/cart/items")
async def create_cart_item(
    telegram_id: int,
    payload: CartItemCreate,
    session: Session,
    event_code: str | None = None,
) -> dict:
    try:
        user = await _user_by_telegram(session, telegram_id)
        event = await get_active_event(session, event_code)
        cart = await add_to_cart(
            session,
            user.id,
            event.id,
            payload.menu_item_id,
            payload.quantity,
            payload.modifier_ids,
            payload.comment,
        )
        return cart_to_dict(cart, user.language)
    except DomainError as exc:
        raise domain_http_error(exc) from exc


@router.delete("/users/{telegram_id}/cart/items/{item_id}", status_code=204)
async def delete_cart_item(
    telegram_id: int,
    item_id: int,
    session: Session,
    expected_quantity: int | None = Query(default=None, ge=1),
) -> None:
    try:
        user = await _user_by_telegram(session, telegram_id)
        await remove_cart_item(
            session,
            user.id,
            item_id,
            expected_quantity=expected_quantity,
        )
    except DomainError as exc:
        raise domain_http_error(exc) from exc


@router.patch("/users/{telegram_id}/cart/items/{item_id}")
async def update_cart_item(
    telegram_id: int,
    item_id: int,
    payload: CartItemUpdate,
    session: Session,
) -> dict:
    try:
        user = await _user_by_telegram(session, telegram_id)
        cart = await update_cart_item_quantity(
            session,
            user.id,
            item_id,
            payload.quantity,
            expected_quantity=payload.expected_quantity,
        )
        return cart_to_dict(cart, user.language)
    except DomainError as exc:
        raise domain_http_error(exc) from exc


@router.post("/orders")
async def create_order(
    payload: OrderCreate,
    session: Session,
    event_code: str | None = Query(default=None),
) -> dict:
    try:
        user = await _user_by_telegram(session, payload.telegram_id)
        event = await get_active_event(session, event_code)
        order = await submit_cart(session, user.id, event.id, payload.comment)
        return order_to_dict(order, user.language)
    except DomainError as exc:
        raise domain_http_error(exc) from exc


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: int,
    telegram_id: int,
    session: Session,
    expected_version: int | None = Query(default=None, ge=1),
) -> dict:
    try:
        user = await _user_by_telegram(session, telegram_id)
        order = await get_order(session, order_id)
        if order.user_id != user.id:
            from app.services.orders import NotFoundError

            raise NotFoundError("Заказ не найден")
        if order.status != OrderStatus.SUBMITTED.value:
            raise ConflictError("Бармен уже принял заказ — отменить его самостоятельно нельзя")
        order = await transition_order(
            session,
            order.id,
            OrderStatus.CANCELLED,
            actor=f"guest:{telegram_id}",
            expected_version=expected_version or order.version,
        )
        return order_to_dict(order, user.language)
    except DomainError as exc:
        raise domain_http_error(exc) from exc


@router.post("/orders/{order_id}/edit")
async def edit_order(
    order_id: int,
    payload: OrderEditRequest,
    session: Session,
) -> dict:
    try:
        user = await _user_by_telegram(session, payload.telegram_id)
        order, cart = await reopen_order_for_edit(
            session,
            order_id,
            user.id,
            expected_version=payload.expected_version,
            actor=f"guest:{payload.telegram_id}",
        )
        return {
            "cancelled_order": order_to_dict(order, user.language),
            "cart": cart_to_dict(cart, user.language),
        }
    except DomainError as exc:
        raise domain_http_error(exc) from exc


@router.post("/recommendations")
async def recommendations(payload: RecommendationRequest, session: Session) -> dict:
    try:
        event = await get_active_event(session, payload.event_code)
        entries = await list_event_menu(session, event.id)
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    candidates = [
        RecommendationCandidate(
            id=entry.menu_item.id,
            name=(
                entry.menu_item.name_en
                if payload.language == Language.EN
                else entry.menu_item.name_ru
            ),
            profile=entry.menu_item.taste_profile,
            available=entry.is_available,
        )
        for entry in entries
    ]
    ranked = rank_recommendations(payload.text, candidates)
    return {
        "items": [
            {"id": candidate.id, "name": candidate.name, "score": score, "reasons": reasons}
            for candidate, score, reasons in ranked
        ]
    }
