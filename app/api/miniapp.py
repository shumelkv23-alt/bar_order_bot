"""Authenticated guest endpoints for the Telegram Mini App."""

import logging
import secrets
from collections import defaultdict, deque
from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.domain import Language
from app.models import Order, User
from app.services.miniapp_auth import InvalidInitData, verify_init_data
from app.services.miniapp_leaderboard import leaderboard, set_name_consent
from app.services.orders import (
    ConflictError,
    DomainError,
    NotFoundError,
    add_to_cart,
    cart_to_dict,
    get_active_event,
    get_cart,
    get_user_active_order,
    list_event_menu,
    remove_cart_item,
    set_user_language,
    submit_cart,
    update_cart_item_details,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/miniapp", tags=["miniapp"])
Session = Annotated[AsyncSession, Depends(get_session)]
_rate_buckets: dict[tuple[int, str], deque[float]] = defaultdict(deque)


def _rate_limit(user_id: int, action: str, limit: int, period: int = 60) -> None:
    """Single-process guard for the current one-worker deployment."""
    now = monotonic()
    bucket = _rate_buckets[(user_id, action)]
    while bucket and bucket[0] < now - period:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limited",
                "message": "Слишком много запросов. Попробуйте чуть позже",
                "details": {},
            },
        )
    bucket.append(now)


class AddItem(BaseModel):
    menu_item_id: int
    quantity: int = Field(ge=1, le=10)
    modifier_ids: list[int] = Field(default_factory=list)
    comment: str = Field(default="", max_length=300)


class ChangeItem(BaseModel):
    quantity: int = Field(ge=1, le=10)
    modifier_ids: list[int] = Field(default_factory=list)
    comment: str = Field(default="", max_length=300)
    expected_quantity: int | None = Field(default=None, ge=1)


class SubmitOrder(BaseModel):
    comment: str = Field(default="", max_length=300)
    idempotency_key: UUID


class MysteryRequest(BaseModel):
    category_id: int | None = None
    exclude_menu_item_id: int | None = None


class LanguageRequest(BaseModel):
    language: Language


class PrivacyRequest(BaseModel):
    show_telegram_name: bool


def _english_error(exc: DomainError) -> str:
    message = str(exc)
    if message.startswith("У вас уже есть активный заказ "):
        return "You already have active order " + message.rsplit(" ", 1)[-1]
    if message.startswith("Одной позиции можно выбрать до "):
        return message.replace("Одной позиции можно выбрать до ", "Up to ").replace(
            " штук", " units of one item"
        )
    if message.startswith("В одном заказе можно выбрать до "):
        return message.replace("В одном заказе можно выбрать до ", "Up to ").replace(
            " единиц", " units per order"
        )
    if message.startswith("Изменились добавки"):
        return "Options changed. Please review your cart"
    if message.startswith("Недоступны:"):
        return "Some items are unavailable. Please review your cart"
    translations = {
        "Приём заказов сейчас закрыт": "Ordering is paused",
        "Позиция сейчас недоступна": "This item is unavailable",
        "Позиция не входит в меню мероприятия": "This item is not on the event menu",
        "Выбран недоступный модификатор": "An option is no longer available",
        "Для одного типа можно выбрать только один вариант": "Choose one option per group",
        "Модификатор выбран повторно": "An option was selected twice",
        "Корзина пуста": "Your order is empty",
        "Корзина уже изменилась. Откройте её заново": "Your cart changed. Please refresh it",
        "Позиция корзины не найдена": "Cart item not found",
        "Активное мероприятие не найдено": "No active event",
        "В этой категории пока нет доступных позиций": "No available items in this category",
    }
    return translations.get(message, "Please refresh and try again")


def _error(exc: DomainError, language: str = "ru") -> HTTPException:
    code = (
        "not_found"
        if isinstance(exc, NotFoundError)
        else "conflict"
        if isinstance(exc, ConflictError)
        else "validation_error"
    )
    status_code = 404 if code == "not_found" else 409 if code == "conflict" else 422
    message = _english_error(exc) if language == "en" else str(exc)
    return HTTPException(status_code, detail={"code": code, "message": message, "details": {}})


async def current_user(
    session: Session,
    response: Response,
    x_telegram_init_data: str | None = Header(default=None),
) -> User:
    response.headers["Cache-Control"] = "no-store"
    try:
        guest = verify_init_data(x_telegram_init_data or "", get_settings().bot_token or "")
    except InvalidInitData as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_telegram_data",
                "message": "Откройте приложение из бота",
                "details": {},
            },
        ) from exc
    user = await session.scalar(select(User).where(User.telegram_id == guest.telegram_id))
    if user is None:
        user = User(
            telegram_id=guest.telegram_id,
            display_name=guest.display_name,
            username=guest.username,
            language=guest.language,
        )
        session.add(user)
    else:
        if not user.is_active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Guest access disabled")
        user.display_name = guest.display_name
        user.username = guest.username
    await session.commit()
    await session.refresh(user)
    return user


Guest = Annotated[User, Depends(current_user)]


def _item_view(entry, language: str) -> dict:
    item = entry.menu_item
    return {
        "id": item.id,
        "category_id": item.category_id,
        "name": item.name_en if language == "en" else item.name_ru,
        "description": item.description_en if language == "en" else item.description_ru,
        "ingredients": item.ingredients_en if language == "en" else item.ingredients_ru,
        "image_url": item.image_url
        if item.image_url and item.image_url.startswith("https://")
        else None,
        "is_alcoholic": item.is_alcoholic,
        "modifiers": [
            {
                "id": link.modifier.id,
                "name": link.modifier.name_en if language == "en" else link.modifier.name_ru,
                "kind": link.modifier.kind,
            }
            for link in item.allowed_modifiers
            if link.modifier.is_active
        ],
    }


def _cart_view(cart, language: str) -> dict:
    payload = cart_to_dict(cart, language)
    for serialized, original in zip(payload["items"], cart.items, strict=True):
        serialized["modifier_ids"] = [modifier["id"] for modifier in original.selected_modifiers]
    payload["total_quantity"] = sum(item["quantity"] for item in payload["items"])
    return payload


def _order_view(order: Order, language: str) -> dict:
    return {
        "id": order.id,
        "public_number": order.public_number,
        "status": order.status,
        "version": order.version,
        "created_at": order.created_at.isoformat(),
        "comment": order.comment,
        "items": [
            {
                "name": item.name_en_snapshot if language == "en" else item.name_ru_snapshot,
                "quantity": item.quantity,
                "modifiers": [
                    modifier.get("name_en" if language == "en" else "name_ru", "")
                    for modifier in item.modifiers_snapshot
                ],
                "comment": item.comment,
            }
            for item in order.items
        ],
    }


@router.get("/bootstrap")
async def bootstrap(session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
    except NotFoundError:
        return {
            "user": {"display_name": user.display_name, "language": user.language},
            "event": None,
            "cart_count": 0,
            "active_order": None,
            "latest_order": None,
        }
    cart = await get_cart(session, user.id, event.id)
    active = await get_user_active_order(session, user.id, event.id)
    latest = await _latest_order(session, user.id, event.id)
    return {
        "user": {"display_name": user.display_name, "language": user.language},
        "event": {
            "id": event.id,
            "name": event.name,
            "orders_enabled": event.orders_enabled,
            "max_items_per_order": event.max_items_per_order,
            "max_same_item": event.max_same_item,
        },
        "cart_count": sum(item.quantity for item in cart.items),
        "active_order": _order_view(active, user.language) if active else None,
        "latest_order": _order_view(latest, user.language) if latest else None,
    }


@router.put("/me/language")
async def change_language(payload: LanguageRequest, session: Session, user: Guest) -> dict:
    await set_user_language(session, user.telegram_id, payload.language.value)
    return {"language": payload.language.value}


@router.get("/menu")
async def menu(session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
        entries = await list_event_menu(session, event.id)
    except DomainError as exc:
        raise _error(exc, user.language) from exc
    categories: dict[int, dict] = {}
    for entry in entries:
        category = entry.menu_item.category
        group = categories.setdefault(
            category.id,
            {
                "id": category.id,
                "name": category.name_en if user.language == "en" else category.name_ru,
                "sort_order": category.sort_order,
                "items": [],
            },
        )
        group["items"].append(_item_view(entry, user.language))
    return {
        "event_id": event.id,
        "categories": sorted(
            categories.values(), key=lambda group: (group["sort_order"], group["id"])
        ),
    }


@router.get("/cart")
async def read_cart(session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
        return _cart_view(await get_cart(session, user.id, event.id), user.language)
    except DomainError as exc:
        raise _error(exc, user.language) from exc


@router.post("/cart/items")
async def add_item(payload: AddItem, session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
        cart = await add_to_cart(
            session,
            user.id,
            event.id,
            payload.menu_item_id,
            payload.quantity,
            payload.modifier_ids,
            payload.comment,
        )
        return _cart_view(cart, user.language)
    except DomainError as exc:
        raise _error(exc, user.language) from exc


@router.patch("/cart/items/{item_id}")
async def change_item(item_id: int, payload: ChangeItem, session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
        cart = await update_cart_item_details(
            session,
            user.id,
            event.id,
            item_id,
            payload.quantity,
            payload.modifier_ids,
            payload.comment,
            expected_quantity=payload.expected_quantity,
        )
        return _cart_view(cart, user.language)
    except DomainError as exc:
        raise _error(exc, user.language) from exc


@router.delete("/cart/items/{item_id}")
async def delete_item(item_id: int, session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
        cart = await get_cart(session, user.id, event.id)
        if not any(item.id == item_id for item in cart.items):
            raise NotFoundError("Позиция корзины не найдена")
        await remove_cart_item(session, user.id, item_id)
        return _cart_view(await get_cart(session, user.id, event.id), user.language)
    except DomainError as exc:
        raise _error(exc, user.language) from exc


async def _latest_order(session: AsyncSession, user_id: int, event_id: int) -> Order | None:
    return await session.scalar(
        select(Order)
        .where(Order.user_id == user_id, Order.event_id == event_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(1)
    )


@router.get("/orders/latest")
async def latest_order(session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
        order = await _latest_order(session, user.id, event.id)
        return {"order": _order_view(order, user.language) if order else None}
    except DomainError as exc:
        raise _error(exc, user.language) from exc


@router.post("/orders")
async def create_order(
    payload: SubmitOrder, request: Request, session: Session, user: Guest
) -> dict:
    previous = await session.scalar(
        select(Order).where(
            Order.user_id == user.id,
            Order.idempotency_key == str(payload.idempotency_key),
        )
    )
    if previous:
        return _order_view(previous, user.language)
    _rate_limit(user.id, "order", 10)
    try:
        event = await get_active_event(session)
        result = await submit_cart(
            session,
            user.id,
            event.id,
            payload.comment,
            idempotency_key=str(payload.idempotency_key),
            return_creation=True,
        )
        order, created = result
    except DomainError as exc:
        raise _error(exc, user.language) from exc
    bot = getattr(request.app.state, "bot", None)
    if created and bot:
        try:
            language = user.language
            message = (
                f"Заказ {order.public_number} отправлен бармену."
                if language == "ru"
                else f"Order {order.public_number} was sent to the bartender."
            )
            await bot.send_message(user.telegram_id, message)
        except Exception:
            logger.exception("miniapp_order_confirmation_failed", extra={"order_id": order.id})
    return _order_view(order, user.language)


@router.post("/mystery/generate")
async def mystery(payload: MysteryRequest, session: Session, user: Guest) -> dict:
    _rate_limit(user.id, "mystery", 30)
    try:
        event = await get_active_event(session)
        if not event.orders_enabled:
            raise ConflictError("Приём заказов сейчас закрыт")
        entries = await list_event_menu(session, event.id, category_id=payload.category_id)
        if not entries:
            raise NotFoundError("В этой категории пока нет доступных позиций")
        can_reroll = len(entries) > 1
        if can_reroll and payload.exclude_menu_item_id is not None:
            entries = [
                entry for entry in entries if entry.menu_item_id != payload.exclude_menu_item_id
            ]
        chosen = secrets.choice(entries)
        return {"item": _item_view(chosen, user.language), "can_reroll": can_reroll}
    except DomainError as exc:
        raise _error(exc, user.language) from exc


@router.get("/leaderboard")
async def read_leaderboard(session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
        return await leaderboard(session, event.id, user.id, user.language)
    except DomainError as exc:
        raise _error(exc, user.language) from exc


@router.put("/leaderboard/privacy")
async def privacy(payload: PrivacyRequest, session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
        result = await set_name_consent(session, event.id, user.id, payload.show_telegram_name)
        return {"show_telegram_name": result}
    except DomainError as exc:
        raise _error(exc, user.language) from exc
