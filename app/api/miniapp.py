"""Authenticated guest endpoints for the Telegram Mini App."""

import logging
import secrets
from collections import defaultdict, deque
from datetime import UTC, datetime
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
from app.models import EventMenuItem, Order, SecretOffer, User
from app.services.achievements import (
    achievement_collection,
    award_achievement,
    discovery_token,
    valid_discovery_token,
)
from app.services.miniapp_auth import InvalidInitData, verify_init_data
from app.services.miniapp_leaderboard import leaderboard, set_name_consent
from app.services.orders import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
    add_to_cart,
    cart_fingerprint,
    cart_to_dict,
    get_active_event,
    get_cart,
    get_user_active_order,
    list_event_menu,
    remove_cart_item,
    repeat_order_to_cart,
    set_user_language,
    submit_cart,
    update_cart_item_details,
)
from app.services.secret_menu import (
    answer_matches,
    as_utc,
    offer_available,
    unlock_offer,
    user_has_unlock,
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


class RepeatOrder(BaseModel):
    expected_cart_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    replace_existing: bool = False


class MysteryRequest(BaseModel):
    category_id: int | None = None
    exclude_menu_item_id: int | None = None


class LanguageRequest(BaseModel):
    language: Language


class PrivacyRequest(BaseModel):
    show_telegram_name: bool


class QuizAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=20)


class DiscoveryProof(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class SecretAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=120)


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
        "Подтвердите замену корзины": "Confirm that you want to replace your cart",
        "Позиция корзины не найдена": "Cart item not found",
        "Активное мероприятие не найдено": "No active event",
        "Заказ не найден": "Order not found",
        "Сначала дождитесь завершения текущего заказа": "Wait for your current order to finish",
        "Предыдущий заказ пуст": "The previous order is empty",
        "Предыдущий заказ превышает текущий лимит позиций": (
            "The previous order exceeds the current item limit"
        ),
        "Предыдущий заказ превышает текущий лимит одной позиции": (
            "The previous order exceeds the current per-item limit"
        ),
        "Предыдущий заказ содержит недоступные позиции": (
            "The previous order contains unavailable items"
        ),
        "Предыдущий заказ содержит недоступные позиции или добавки": (
            "The previous order contains unavailable items or options"
        ),
        "В этой категории пока нет доступных позиций": "No available items in this category",
        "Сначала найдите знак на экране Мистери": "Find the mark on the Mystery screen first",
        "Секретная позиция недоступна": "This secret item is unavailable",
        "Секретная позиция закончилась. Обновите корзину": (
            "This secret item sold out. Refresh your cart"
        ),
        "Неверный ответ на загадку": "That answer does not solve the riddle",
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
    payload["fingerprint"] = cart_fingerprint(cart)
    return payload


def _order_view(order: Order, language: str) -> dict:
    return {
        "id": order.id,
        "public_number": order.public_number,
        "status": order.status,
        "status_automatically": order.status_automatically,
        "completed_automatically": order.completed_automatically,
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


async def _secret_view(
    session: AsyncSession, offer: SecretOffer, user: User, *, orders_enabled: bool = True
) -> dict:
    unlocked = await user_has_unlock(session, offer.id, user.id)
    available = orders_enabled and offer_available(offer)
    entry = await session.scalar(select(EventMenuItem).where(
        EventMenuItem.event_id == offer.event_id,
        EventMenuItem.menu_item_id == offer.menu_item_id,
    ))
    available = available and entry is not None and entry.is_available
    if available:
        unavailable_reason = None
    elif offer.portions_used >= offer.portions_total:
        unavailable_reason = "sold_out"
    elif datetime.now(UTC) < as_utc(offer.available_from):
        unavailable_reason = "upcoming"
    else:
        unavailable_reason = "unavailable"
    return {
        "id": offer.id,
        "riddle": offer.riddle_en if user.language == "en" else offer.riddle_ru,
        "available_from": as_utc(offer.available_from).isoformat(),
        "available_until": as_utc(offer.available_until).isoformat(),
        "remaining": max(0, offer.portions_total - offer.portions_used),
        "available": available,
        "unavailable_reason": unavailable_reason,
        "unlocked": unlocked,
        "item": _item_view(entry, user.language) if unlocked and available else None,
    }


@router.get("/secret-menu")
async def secret_menu(session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
    except DomainError as exc:
        raise _error(exc, user.language) from exc
    offers = (await session.scalars(select(SecretOffer).where(
        SecretOffer.event_id == event.id,
        SecretOffer.is_active.is_(True),
        SecretOffer.available_until > datetime.now(UTC),
    ).order_by(SecretOffer.available_from, SecretOffer.id))).all()
    return {
        "offers": [
            await _secret_view(session, offer, user, orders_enabled=event.orders_enabled)
            for offer in offers
        ]
    }


@router.post("/secret-menu/{offer_id}/unlock")
async def unlock_secret(
    offer_id: int, payload: SecretAnswer, session: Session, user: Guest
) -> dict:
    _rate_limit(user.id, "secret_answer", 8)
    try:
        event = await get_active_event(session)
        if not event.orders_enabled:
            raise ConflictError("Приём заказов сейчас закрыт")
        offer = await session.scalar(select(SecretOffer).where(
            SecretOffer.id == offer_id, SecretOffer.event_id == event.id
        ))
        if offer is None:
            raise NotFoundError("Секретная позиция недоступна")
        if not (await _secret_view(session, offer, user))["available"]:
            raise ConflictError("Секретная позиция недоступна")
        if not answer_matches(offer, payload.answer):
            raise ValidationError("Неверный ответ на загадку")
        await unlock_offer(session, offer.id, user.id)
        awarded = await award_achievement(session, event.id, user.id, "inner_circle")
        await session.commit()
        return {
            **(await _secret_view(session, offer, user)),
            "new_achievement": "inner_circle" if awarded else None,
        }
    except DomainError as exc:
        raise _error(exc, user.language) from exc


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


@router.post("/orders/{order_id}/repeat")
async def repeat_order(order_id: int, payload: RepeatOrder, session: Session, user: Guest) -> dict:
    _rate_limit(user.id, "repeat_order", 10)
    try:
        event = await get_active_event(session)
        cart, order_comment, awarded = await repeat_order_to_cart(
            session, user.id, event.id, order_id,
            expected_cart_fingerprint=payload.expected_cart_fingerprint,
            replace_existing=payload.replace_existing,
        )
        return {
            "cart": _cart_view(cart, user.language),
            "order_comment": order_comment,
            "new_achievement": "encore" if awarded else None,
        }
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
        return {**_order_view(previous, user.language), "new_achievements": []}
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
        order, created, new_achievements = result
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
    return {
        **_order_view(order, user.language),
        "new_achievements": new_achievements,
    }


@router.get("/achievements")
async def achievements(session: Session, user: Guest) -> dict:
    try:
        event = await get_active_event(session)
    except DomainError as exc:
        raise _error(exc, user.language) from exc
    # Give existing guests their first-order badge after this feature is deployed.
    prior_order = await session.scalar(
        select(Order.id).where(Order.event_id == event.id, Order.user_id == user.id).limit(1)
    )
    if prior_order is not None and await award_achievement(
        session, event.id, user.id, "first_contact"
    ):
        await session.commit()
    return await achievement_collection(session, event.id, user.id, user.language)


@router.post("/achievements/discover")
async def discover_achievement(payload: DiscoveryProof, session: Session, user: Guest) -> dict:
    _rate_limit(user.id, "discover", 5)
    try:
        event = await get_active_event(session)
    except DomainError as exc:
        raise _error(exc, user.language) from exc
    if not valid_discovery_token(
        payload.token, event.id, user.id, get_settings().bot_token or ""
    ):
        raise _error(ValidationError("Сначала найдите знак на экране Мистери"), user.language)
    awarded = await award_achievement(session, event.id, user.id, "pathfinder")
    await session.commit()
    return {"new_achievement": "pathfinder" if awarded else None}


@router.post("/achievements/quiz")
async def answer_quiz(payload: QuizAnswer, session: Session, user: Guest) -> dict:
    _rate_limit(user.id, "quiz", 10)
    try:
        event = await get_active_event(session)
    except DomainError as exc:
        raise _error(exc, user.language) from exc
    correct = payload.answer == "mint"
    awarded = False
    if correct:
        awarded = await award_achievement(session, event.id, user.id, "connoisseur")
        await session.commit()
    return {"correct": correct, "new_achievement": "connoisseur" if awarded else None}


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
        awarded = await award_achievement(session, event.id, user.id, "lucky_draw")
        await session.commit()
        return {
            "item": _item_view(chosen, user.language),
            "can_reroll": can_reroll,
            "discovery_token": discovery_token(
                event.id, user.id, chosen.menu_item_id, get_settings().bot_token or ""
            ),
            "new_achievement": "lucky_draw" if awarded else None,
        }
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
