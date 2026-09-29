from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import StringIO
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain import (
    ACTIVE_ORDER_STATUSES,
    EventStatus,
    Language,
    OrderStatus,
    SpecialRequestStatus,
    ensure_transition,
)
from app.models import (
    AuditLog,
    Cart,
    CartItem,
    Category,
    Event,
    EventMenuItem,
    MenuItem,
    Order,
    OrderItem,
    OrderStatusHistory,
    SpecialRequest,
    User,
)
from app.services.achievements import award_achievement


class DomainError(RuntimeError):
    pass


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class ValidationError(DomainError):
    pass


SPECIAL_REQUEST_TRANSITIONS = {
    SpecialRequestStatus.SUBMITTED: {
        SpecialRequestStatus.ACCEPTED,
        SpecialRequestStatus.REJECTED,
    },
    SpecialRequestStatus.ACCEPTED: {
        SpecialRequestStatus.FULFILLED,
        SpecialRequestStatus.REJECTED,
    },
    SpecialRequestStatus.FULFILLED: set(),
    SpecialRequestStatus.REJECTED: set(),
}


def special_request_to_dict(request: SpecialRequest, language: str = "ru") -> dict[str, Any]:
    suggested_name = None
    if request.suggested_menu_item:
        suggested_name = (
            request.suggested_menu_item.name_en
            if language == Language.EN.value
            else request.suggested_menu_item.name_ru
        )
    return {
        "id": request.id,
        "event_id": request.event_id,
        "telegram_id": request.user.telegram_id,
        "guest": request.user.display_name,
        "language": request.user.language,
        "request_text": request.request_text,
        "source_transcript": request.source_transcript,
        "quantity": request.quantity,
        "status": request.status,
        "bartender_note": request.bartender_note,
        "suggested_menu_item_id": request.suggested_menu_item_id,
        "suggested_name": suggested_name,
        "created_at": request.created_at.isoformat(),
        "updated_at": request.updated_at.isoformat(),
    }


async def create_special_request(
    session: AsyncSession,
    user_id: int,
    event_id: int,
    request_text: str,
    quantity: int = 1,
    *,
    source_transcript: str = "",
) -> SpecialRequest:
    event = await session.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update(of=Event)
        .execution_options(populate_existing=True)
    )
    if not event or event.status != EventStatus.ACTIVE.value or not event.orders_enabled:
        raise ConflictError("Приём заказов сейчас закрыт")
    if not request_text.strip():
        raise ValidationError("Особый запрос не может быть пустым")
    if quantity < 1 or quantity > event.max_same_item:
        raise ValidationError(f"Допустимо от 1 до {event.max_same_item} единиц")
    request = SpecialRequest(
        event_id=event_id,
        user_id=user_id,
        request_text=request_text.strip()[:300],
        source_transcript=source_transcript.strip(),
        quantity=quantity,
    )
    session.add(request)
    await session.flush()
    session.add(
        AuditLog(
            actor=f"guest:{user_id}",
            action="special_request.created",
            entity_type="special_request",
            entity_id=str(request.id),
            payload={"request_text": request.request_text, "quantity": quantity},
        )
    )
    await session.commit()
    return await session.scalar(select(SpecialRequest).where(SpecialRequest.id == request.id))


async def list_special_requests(
    session: AsyncSession,
    event_id: int,
    statuses: list[str] | None = None,
) -> list[SpecialRequest]:
    statement = select(SpecialRequest).where(SpecialRequest.event_id == event_id)
    if statuses:
        statement = statement.where(SpecialRequest.status.in_(statuses))
    statement = statement.order_by(SpecialRequest.created_at, SpecialRequest.id)
    return list((await session.scalars(statement)).unique().all())


async def transition_special_request(
    session: AsyncSession,
    request_id: int,
    target: SpecialRequestStatus,
    *,
    actor: str,
    note: str = "",
) -> SpecialRequest:
    request = await session.scalar(
        select(SpecialRequest).where(SpecialRequest.id == request_id).with_for_update()
    )
    if not request:
        raise NotFoundError("Особый запрос не найден")
    current = SpecialRequestStatus(request.status)
    if target not in SPECIAL_REQUEST_TRANSITIONS[current]:
        raise ConflictError("Статус особого запроса уже изменился")
    request.status = target.value
    request.bartender_note = note.strip()[:300]
    session.add(
        AuditLog(
            actor=actor,
            action="special_request.status_changed",
            entity_type="special_request",
            entity_id=str(request.id),
            payload={"from": current.value, "to": target.value, "note": request.bartender_note},
        )
    )
    await session.commit()
    return await session.scalar(select(SpecialRequest).where(SpecialRequest.id == request.id))


def utc_now() -> datetime:
    return datetime.now(UTC)


async def get_active_event(session: AsyncSession, code: str | None = None) -> Event:
    statement = select(Event).where(Event.status == EventStatus.ACTIVE.value)
    if code:
        statement = statement.where(Event.code == code)
    statement = statement.order_by(Event.starts_at.desc())
    event = await session.scalar(statement)
    if not event:
        raise NotFoundError("Активное мероприятие не найдено")
    return event


async def upsert_user(
    session: AsyncSession,
    telegram_id: int,
    display_name: str,
    username: str | None = None,
    language: str = Language.RU.value,
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(
            telegram_id=telegram_id,
            display_name=display_name,
            username=username,
            language=language,
        )
        session.add(user)
    else:
        user.display_name = display_name
        user.username = username
        if language:
            user.language = language
    await session.commit()
    await session.refresh(user)
    return user


async def set_user_language(session: AsyncSession, telegram_id: int, language: str) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user:
        raise NotFoundError("Пользователь не найден")
    user.language = Language(language).value
    await session.commit()
    return user


async def list_event_menu(
    session: AsyncSession,
    event_id: int,
    *,
    only_available: bool = True,
    category_id: int | None = None,
) -> list[EventMenuItem]:
    statement = (
        select(EventMenuItem)
        .join(EventMenuItem.menu_item)
        .join(MenuItem.category)
        .where(
            EventMenuItem.event_id == event_id,
            MenuItem.is_archived.is_(False),
            Category.is_active.is_(True),
        )
        .options(selectinload(EventMenuItem.menu_item).selectinload(MenuItem.allowed_modifiers))
        .order_by(MenuItem.sort_order, MenuItem.id)
    )
    if only_available:
        statement = statement.where(EventMenuItem.is_available.is_(True))
    if category_id is not None:
        statement = statement.where(MenuItem.category_id == category_id)
    return list((await session.scalars(statement)).unique().all())


async def get_menu_entry(session: AsyncSession, event_id: int, menu_item_id: int) -> EventMenuItem:
    statement = (
        select(EventMenuItem)
        .where(
            EventMenuItem.event_id == event_id,
            EventMenuItem.menu_item_id == menu_item_id,
        )
        .options(selectinload(EventMenuItem.menu_item).selectinload(MenuItem.allowed_modifiers))
    )
    entry = await session.scalar(statement)
    if not entry:
        raise NotFoundError("Позиция не входит в меню мероприятия")
    if entry.menu_item.is_archived or not entry.menu_item.category.is_active:
        raise ConflictError("Позиция сейчас недоступна")
    return entry


def _selected_modifiers(entry: EventMenuItem, modifier_ids: list[int]) -> list[dict[str, Any]]:
    if len(modifier_ids) != len(set(modifier_ids)):
        raise ValidationError("Модификатор выбран повторно")
    allowed = {
        link.modifier_id: link.modifier
        for link in entry.menu_item.allowed_modifiers
        if link.modifier.is_active
    }
    if set(modifier_ids) - set(allowed):
        raise ValidationError("Выбран недоступный модификатор")
    selected_kinds = [
        allowed[modifier_id].kind
        for modifier_id in modifier_ids
        if allowed[modifier_id].kind in {"ice", "variant"}
    ]
    if len(selected_kinds) != len(set(selected_kinds)):
        raise ValidationError("Для одного типа можно выбрать только один вариант")
    return [
        {
            "id": modifier.id,
            "name_ru": modifier.name_ru,
            "name_en": modifier.name_en,
            "kind": modifier.kind,
        }
        for modifier in (allowed[modifier_id] for modifier_id in modifier_ids)
    ]


async def get_or_create_cart(session: AsyncSession, user_id: int, event_id: int) -> Cart:
    statement = (
        select(Cart)
        .where(Cart.user_id == user_id, Cart.event_id == event_id)
        .options(selectinload(Cart.items).selectinload(CartItem.menu_item))
        .execution_options(populate_existing=True)
    )
    cart = await session.scalar(statement)
    if cart is None:
        cart = Cart(user_id=user_id, event_id=event_id, items=[])
        session.add(cart)
        await session.flush()
    return cart


async def get_cart(session: AsyncSession, user_id: int, event_id: int) -> Cart:
    cart = await get_or_create_cart(session, user_id, event_id)
    await session.commit()
    return cart


async def add_to_cart(
    session: AsyncSession,
    user_id: int,
    event_id: int,
    menu_item_id: int,
    quantity: int,
    modifier_ids: list[int] | None = None,
    comment: str = "",
) -> Cart:
    modifier_ids = list(dict.fromkeys(modifier_ids or []))
    event = await session.get(Event, event_id)
    if not event or event.status != EventStatus.ACTIVE.value or not event.orders_enabled:
        raise ConflictError("Приём заказов сейчас закрыт")
    if quantity < 1 or quantity > event.max_same_item:
        raise ValidationError(f"Допустимо от 1 до {event.max_same_item} единиц одной позиции")

    entry = await get_menu_entry(session, event_id, menu_item_id)
    if not entry.is_available:
        raise ConflictError("Позиция сейчас недоступна")

    selected_modifiers = _selected_modifiers(entry, modifier_ids)

    cart = await get_or_create_cart(session, user_id, event_id)
    current_total = sum(item.quantity for item in cart.items)
    if current_total + quantity > event.max_items_per_order:
        raise ValidationError(f"В одном заказе можно выбрать до {event.max_items_per_order} единиц")
    same_item_total = sum(item.quantity for item in cart.items if item.menu_item_id == menu_item_id)
    if same_item_total + quantity > event.max_same_item:
        raise ValidationError(f"Одной позиции можно выбрать до {event.max_same_item} штук")

    matching = next(
        (
            item
            for item in cart.items
            if item.menu_item_id == menu_item_id
            and item.selected_modifiers == selected_modifiers
            and item.comment == comment
        ),
        None,
    )
    if matching:
        if matching.quantity + quantity > event.max_same_item:
            raise ValidationError(f"Одной позиции можно выбрать до {event.max_same_item} штук")
        matching.quantity += quantity
    else:
        cart.items.append(
            CartItem(
                menu_item_id=menu_item_id,
                quantity=quantity,
                selected_modifiers=selected_modifiers,
                comment=comment,
            )
        )
    await session.commit()
    return await get_or_create_cart(session, user_id, event_id)


async def update_cart_item_quantity(
    session: AsyncSession,
    user_id: int,
    item_id: int,
    quantity: int,
    *,
    expected_quantity: int | None = None,
) -> Cart:
    row = (
        await session.execute(
            select(CartItem, Cart).join(Cart).where(CartItem.id == item_id, Cart.user_id == user_id)
        )
    ).one_or_none()
    if not row:
        raise NotFoundError("Позиция корзины не найдена")
    cart_item, cart = row
    if expected_quantity is not None and cart_item.quantity != expected_quantity:
        raise ConflictError("Корзина уже изменилась. Откройте её заново")
    if quantity < 1:
        raise ValidationError("Количество не может быть меньше одного")

    event = await session.get(Event, cart.event_id)
    if not event:
        raise NotFoundError("Мероприятие не найдено")
    if quantity > cart_item.quantity:
        if event.status != EventStatus.ACTIVE.value or not event.orders_enabled:
            raise ConflictError("Приём заказов сейчас закрыт")
        if quantity > event.max_same_item:
            raise ValidationError(f"Одной позиции можно выбрать до {event.max_same_item} штук")
        cart_with_items = await get_or_create_cart(session, user_id, cart.event_id)
        new_total = sum(item.quantity for item in cart_with_items.items)
        new_total += quantity - cart_item.quantity
        if new_total > event.max_items_per_order:
            raise ValidationError(
                f"В одном заказе можно выбрать до {event.max_items_per_order} единиц"
            )

    cart_item.quantity = quantity
    await session.commit()
    return await get_or_create_cart(session, user_id, cart.event_id)


async def remove_cart_item(
    session: AsyncSession,
    user_id: int,
    item_id: int,
    *,
    expected_quantity: int | None = None,
) -> None:
    row = (
        await session.execute(
            select(CartItem, Cart).join(Cart).where(CartItem.id == item_id, Cart.user_id == user_id)
        )
    ).one_or_none()
    if not row:
        raise NotFoundError("Позиция корзины не найдена")
    cart_item, cart = row
    if expected_quantity is not None and cart_item.quantity != expected_quantity:
        raise ConflictError("Корзина уже изменилась. Откройте её заново")
    await session.delete(cart_item)
    await session.commit()
    await get_or_create_cart(session, user_id, cart.event_id)


async def update_cart_item_details(
    session: AsyncSession,
    user_id: int,
    event_id: int,
    item_id: int,
    quantity: int,
    modifier_ids: list[int],
    comment: str,
    *,
    expected_quantity: int | None = None,
) -> Cart:
    row = (
        await session.execute(
            select(CartItem, Cart)
            .join(Cart)
            .where(CartItem.id == item_id, Cart.user_id == user_id, Cart.event_id == event_id)
            .with_for_update(of=CartItem)
        )
    ).one_or_none()
    if not row:
        raise NotFoundError("Позиция корзины не найдена")
    cart_item, _ = row
    if expected_quantity is not None and cart_item.quantity != expected_quantity:
        raise ConflictError("Корзина уже изменилась. Откройте её заново")
    event = await session.get(Event, event_id)
    if not event or event.status != EventStatus.ACTIVE.value or not event.orders_enabled:
        raise ConflictError("Приём заказов сейчас закрыт")
    if quantity < 1 or quantity > event.max_same_item:
        raise ValidationError(f"Одной позиции можно выбрать до {event.max_same_item} штук")
    entry = await get_menu_entry(session, event_id, cart_item.menu_item_id)
    if not entry.is_available:
        raise ConflictError("Позиция сейчас недоступна")
    selected = _selected_modifiers(entry, list(modifier_ids))
    cart = await get_or_create_cart(session, user_id, event_id)
    total = sum(item.quantity for item in cart.items) - cart_item.quantity + quantity
    if total > event.max_items_per_order:
        raise ValidationError(f"В одном заказе можно выбрать до {event.max_items_per_order} единиц")
    same_item_total = quantity + sum(
        item.quantity
        for item in cart.items
        if item.id != cart_item.id and item.menu_item_id == cart_item.menu_item_id
    )
    if same_item_total > event.max_same_item:
        raise ValidationError(f"Одной позиции можно выбрать до {event.max_same_item} штук")
    cart_item.quantity = quantity
    cart_item.selected_modifiers = selected
    cart_item.comment = comment[:300]
    await session.commit()
    return await get_or_create_cart(session, user_id, event_id)


async def submit_cart(
    session: AsyncSession,
    user_id: int,
    event_id: int,
    comment: str = "",
    *,
    idempotency_key: str | None = None,
    return_creation: bool = False,
) -> Order | tuple[Order, bool, list[str]]:
    if idempotency_key:
        previous = await session.scalar(
            select(Order).where(
                Order.user_id == user_id,
                Order.event_id == event_id,
                Order.idempotency_key == idempotency_key,
            )
        )
        if previous:
            order = await get_order(session, previous.id)
            return (order, False, []) if return_creation else order
    event = await session.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update(of=Event)
        .execution_options(populate_existing=True)
    )
    if not event or event.status != EventStatus.ACTIVE.value or not event.orders_enabled:
        raise ConflictError("Приём заказов сейчас закрыт")

    if idempotency_key:
        previous = await session.scalar(
            select(Order).where(
                Order.user_id == user_id,
                Order.event_id == event_id,
                Order.idempotency_key == idempotency_key,
            )
        )
        if previous:
            order = await get_order(session, previous.id)
            return (order, False, []) if return_creation else order

    existing = await session.scalar(
        select(Order).where(
            Order.user_id == user_id,
            Order.event_id == event_id,
            Order.status.in_([status.value for status in ACTIVE_ORDER_STATUSES]),
        )
    )
    if existing:
        raise ConflictError(f"У вас уже есть активный заказ {existing.public_number}")

    has_prior_order = await session.scalar(
        select(Order.id).where(Order.user_id == user_id, Order.event_id == event_id).limit(1)
    )

    cart = await get_or_create_cart(session, user_id, event_id)
    if not cart.items:
        raise ValidationError("Корзина пуста")
    if sum(item.quantity for item in cart.items) > event.max_items_per_order:
        raise ValidationError("Превышен лимит позиций")
    quantities_by_item: dict[int, int] = {}
    for cart_item in cart.items:
        quantities_by_item[cart_item.menu_item_id] = (
            quantities_by_item.get(cart_item.menu_item_id, 0) + cart_item.quantity
        )
    if any(quantity > event.max_same_item for quantity in quantities_by_item.values()):
        raise ValidationError(f"Одной позиции можно выбрать до {event.max_same_item} штук")

    availability = {
        entry.menu_item_id: entry.is_available
        for entry in await list_event_menu(session, event_id, only_available=False)
    }
    unavailable = [
        item.menu_item.name_ru for item in cart.items if not availability.get(item.menu_item_id)
    ]
    if unavailable:
        raise ConflictError("Недоступны: " + ", ".join(unavailable))
    for cart_item in cart.items:
        entry = await get_menu_entry(session, event_id, cart_item.menu_item_id)
        try:
            _selected_modifiers(
                entry,
                [modifier["id"] for modifier in cart_item.selected_modifiers],
            )
        except (ValidationError, KeyError, TypeError) as exc:
            raise ConflictError(
                f"Изменились добавки для «{cart_item.menu_item.name_ru}». Проверьте корзину"
            ) from exc

    next_number = await session.scalar(
        update(Event)
        .where(Event.id == event_id)
        .values(last_order_number=Event.last_order_number + 1)
        .returning(Event.last_order_number)
    )
    prefix = (event.code[:1] or "A").upper()
    public_number = f"{prefix}-{int(next_number):03d}"
    order = Order(
        event_id=event_id,
        user_id=user_id,
        public_number=public_number,
        status=OrderStatus.SUBMITTED.value,
        comment=comment,
        idempotency_key=idempotency_key,
    )
    for cart_item in cart.items:
        order.items.append(
            OrderItem(
                menu_item_id=cart_item.menu_item_id,
                name_ru_snapshot=cart_item.menu_item.name_ru,
                name_en_snapshot=cart_item.menu_item.name_en,
                quantity=cart_item.quantity,
                modifiers_snapshot=cart_item.selected_modifiers,
                comment=cart_item.comment,
            )
        )
    order.history.append(
        OrderStatusHistory(from_status=None, to_status=OrderStatus.SUBMITTED.value)
    )
    session.add(order)
    cart.items.clear()
    new_achievements = []
    if has_prior_order is None:
        if await award_achievement(session, event_id, user_id, "first_contact"):
            new_achievements.append("first_contact")
    await session.commit()
    created = await get_order(session, order.id)
    return (created, True, new_achievements) if return_creation else created


async def get_order(
    session: AsyncSession,
    order_id: int,
    *,
    for_update: bool = False,
) -> Order:
    statement = (
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items), selectinload(Order.history))
    )
    if for_update:
        statement = statement.with_for_update(of=Order)
    order = await session.scalar(statement)
    if not order:
        raise NotFoundError("Заказ не найден")
    return order


async def reopen_order_for_edit(
    session: AsyncSession,
    order_id: int,
    user_id: int,
    *,
    expected_version: int,
    actor: str,
) -> tuple[Order, Cart]:
    """Atomically withdraw a submitted order and copy its items back to the cart."""
    order = await get_order(session, order_id, for_update=True)
    if order.user_id != user_id:
        raise NotFoundError("Заказ не найден")
    if order.version != expected_version:
        raise ConflictError("Заказ уже изменился. Обновите его и повторите действие")
    if order.status != OrderStatus.SUBMITTED.value:
        raise ConflictError("Бармен уже принял заказ — изменить его нельзя")

    event = await session.get(Event, order.event_id)
    if not event or event.status != EventStatus.ACTIVE.value or not event.orders_enabled:
        raise ConflictError("Приём заказов сейчас закрыт — изменить заказ нельзя")
    if any(item.menu_item_id is None for item in order.items):
        raise ConflictError("Одну из позиций больше нельзя вернуть в корзину")

    cart = await get_or_create_cart(session, user_id, order.event_id)
    combined_total = sum(item.quantity for item in cart.items) + sum(
        item.quantity for item in order.items
    )
    if combined_total > event.max_items_per_order:
        raise ConflictError(
            "В корзине уже есть позиции. Удалите лишнее и повторите редактирование заказа"
        )

    def line_key(
        menu_item_id: int | None,
        modifiers: list[dict[str, Any]],
        comment: str,
    ) -> tuple[Any, ...]:
        normalized_modifiers = tuple(
            sorted(
                (
                    modifier.get("id"),
                    modifier.get("kind"),
                    modifier.get("name_ru"),
                    modifier.get("name_en"),
                )
                for modifier in modifiers
            )
        )
        return menu_item_id, normalized_modifiers, comment

    quantities: dict[tuple[Any, ...], int] = {}
    for item in cart.items:
        key = line_key(item.menu_item_id, item.selected_modifiers, item.comment)
        quantities[key] = quantities.get(key, 0) + item.quantity
    for item in order.items:
        key = line_key(item.menu_item_id, item.modifiers_snapshot, item.comment)
        quantities[key] = quantities.get(key, 0) + item.quantity
    if any(quantity > event.max_same_item for quantity in quantities.values()):
        raise ConflictError(
            "В корзине уже есть такая позиция. Уменьшите количество и повторите редактирование"
        )

    existing_items = {
        line_key(item.menu_item_id, item.selected_modifiers, item.comment): item
        for item in cart.items
    }
    for order_item in order.items:
        key = line_key(
            order_item.menu_item_id,
            order_item.modifiers_snapshot,
            order_item.comment,
        )
        if matching := existing_items.get(key):
            matching.quantity += order_item.quantity
        else:
            new_item = CartItem(
                menu_item_id=order_item.menu_item_id,
                quantity=order_item.quantity,
                selected_modifiers=order_item.modifiers_snapshot,
                comment=order_item.comment,
            )
            cart.items.append(new_item)
            existing_items[key] = new_item

    previous = order.status
    order.status = OrderStatus.CANCELLED.value
    order.version += 1
    order.cancelled_at = utc_now()
    order.history.append(
        OrderStatusHistory(
            from_status=previous,
            to_status=OrderStatus.CANCELLED.value,
        )
    )
    session.add(
        AuditLog(
            actor=actor,
            action="order_reopened_for_edit",
            entity_type="order",
            entity_id=str(order.id),
            payload={
                "from": previous,
                "to": OrderStatus.CANCELLED.value,
                "restored_items": len(order.items),
            },
        )
    )
    await session.commit()
    restored_order = await get_order(session, order.id)
    restored_cart = await get_or_create_cart(session, user_id, order.event_id)
    return restored_order, restored_cart


async def get_user_active_order(session: AsyncSession, user_id: int, event_id: int) -> Order | None:
    statement = (
        select(Order)
        .where(
            Order.user_id == user_id,
            Order.event_id == event_id,
            Order.status.in_([status.value for status in ACTIVE_ORDER_STATUSES]),
        )
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
    )
    return await session.scalar(statement)


async def list_staff_orders(
    session: AsyncSession,
    event_id: int,
    statuses: list[str] | None = None,
) -> list[Order]:
    statement = (
        select(Order)
        .where(Order.event_id == event_id)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.asc(), Order.id.asc())
    )
    if statuses:
        statement = statement.where(Order.status.in_(statuses))
    return list((await session.scalars(statement)).unique().all())


async def transition_order(
    session: AsyncSession,
    order_id: int,
    target: OrderStatus,
    *,
    actor: str,
    expected_version: int | None = None,
    reason: str = "",
) -> Order:
    order = await get_order(session, order_id, for_update=True)
    if expected_version is not None and order.version != expected_version:
        raise ConflictError("Заказ уже изменился. Обновите очередь и повторите действие")
    try:
        ensure_transition(order.status, target)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    previous = order.status
    order.status = target.value
    order.version += 1
    now = utc_now()
    timestamps = {
        OrderStatus.ACCEPTED: "accepted_at",
        OrderStatus.PREPARING: "preparing_at",
        OrderStatus.READY: "ready_at",
        OrderStatus.COMPLETED: "completed_at",
        OrderStatus.CANCELLED: "cancelled_at",
    }
    if field := timestamps.get(target):
        setattr(order, field, now)
    order.history.append(OrderStatusHistory(from_status=previous, to_status=target.value))
    session.add(
        AuditLog(
            actor=actor,
            action="order_status_changed",
            entity_type="order",
            entity_id=str(order.id),
            payload={"from": previous, "to": target.value, "reason": reason},
        )
    )
    await session.commit()
    return await get_order(session, order.id)


async def analytics_summary(session: AsyncSession, event_id: int) -> dict[str, Any]:
    by_status = dict(
        (
            await session.execute(
                select(Order.status, func.count(Order.id))
                .where(Order.event_id == event_id)
                .group_by(Order.status)
            )
        ).all()
    )
    popular_rows = (
        await session.execute(
            select(OrderItem.name_ru_snapshot, func.sum(OrderItem.quantity).label("quantity"))
            .join(Order)
            .where(Order.event_id == event_id)
            .group_by(OrderItem.name_ru_snapshot)
            .order_by(func.sum(OrderItem.quantity).desc())
            .limit(10)
        )
    ).all()
    unique_guests = await session.scalar(
        select(func.count(func.distinct(Order.user_id))).where(Order.event_id == event_id)
    )
    total_items = await session.scalar(
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order)
        .where(Order.event_id == event_id)
    )
    timing_rows = (
        await session.execute(
            select(Order.created_at, Order.completed_at).where(
                Order.event_id == event_id,
                Order.completed_at.is_not(None),
            )
        )
    ).all()
    order_times = list(
        (
            await session.scalars(
                select(Order.created_at)
                .where(Order.event_id == event_id)
                .order_by(Order.created_at)
            )
        ).all()
    )
    hourly_counts: dict[str, int] = {}
    for created_at in order_times:
        bucket = created_at.replace(minute=0, second=0, microsecond=0).isoformat()
        hourly_counts[bucket] = hourly_counts.get(bucket, 0) + 1

    fulfillment_minutes = [
        (completed_at - created_at).total_seconds() / 60
        for created_at, completed_at in timing_rows
        if completed_at is not None
    ]
    total_orders = sum(by_status.values())
    completed_orders = int(by_status.get(OrderStatus.COMPLETED.value, 0))
    cancelled_orders = int(by_status.get(OrderStatus.CANCELLED.value, 0)) + int(
        by_status.get(OrderStatus.REJECTED.value, 0)
    )
    active_orders = sum(
        int(by_status.get(order_status.value, 0)) for order_status in ACTIVE_ORDER_STATUSES
    )
    return {
        "event_id": event_id,
        "total_orders": total_orders,
        "total_items": int(total_items or 0),
        "unique_guests": unique_guests or 0,
        "active_orders": active_orders,
        "completed_orders": completed_orders,
        "cancelled_orders": cancelled_orders,
        "completion_rate": round(completed_orders / total_orders * 100, 1) if total_orders else 0.0,
        "cancellation_rate": round(cancelled_orders / total_orders * 100, 1)
        if total_orders
        else 0.0,
        "average_items_per_order": round(int(total_items or 0) / total_orders, 2)
        if total_orders
        else 0.0,
        "average_fulfillment_minutes": round(sum(fulfillment_minutes) / len(fulfillment_minutes), 1)
        if fulfillment_minutes
        else None,
        "by_status": by_status,
        "popular_items": [
            {"name": name, "quantity": int(quantity)} for name, quantity in popular_rows
        ],
        "orders_by_hour": [
            {"hour": hour, "orders": count} for hour, count in hourly_counts.items()
        ],
    }


def analytics_to_csv(summary: dict[str, Any]) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["section", "name", "value"])
    for field in (
        "event_id",
        "total_orders",
        "total_items",
        "unique_guests",
        "active_orders",
        "completed_orders",
        "cancelled_orders",
        "completion_rate",
        "cancellation_rate",
        "average_items_per_order",
        "average_fulfillment_minutes",
    ):
        writer.writerow(["summary", field, summary.get(field, "")])
    for name, value in summary.get("by_status", {}).items():
        writer.writerow(["status", name, value])
    for item in summary.get("popular_items", []):
        writer.writerow(["popular_item", item["name"], item["quantity"]])
    for point in summary.get("orders_by_hour", []):
        writer.writerow(["orders_by_hour", point["hour"], point["orders"]])
    return output.getvalue()


def cart_to_dict(cart: Cart, language: str = Language.RU.value) -> dict[str, Any]:
    return {
        "id": cart.id,
        "event_id": cart.event_id,
        "items": [
            {
                "id": item.id,
                "menu_item_id": item.menu_item_id,
                "name": (
                    item.menu_item.name_en
                    if language == Language.EN.value
                    else item.menu_item.name_ru
                ),
                "quantity": item.quantity,
                "modifiers": [
                    modifier.get("name_en" if language == Language.EN.value else "name_ru", "")
                    for modifier in item.selected_modifiers
                ],
                "comment": item.comment,
            }
            for item in cart.items
        ],
    }


def order_to_dict(order: Order, language: str = Language.RU.value) -> dict[str, Any]:
    return {
        "id": order.id,
        "public_number": order.public_number,
        "status": order.status,
        "version": order.version,
        "created_at": order.created_at.isoformat(),
        "guest": order.user.display_name,
        "telegram_id": order.user.telegram_id,
        "comment": order.comment,
        "items": [
            {
                "name": (
                    item.name_en_snapshot
                    if language == Language.EN.value
                    else item.name_ru_snapshot
                ),
                "quantity": item.quantity,
                "modifiers": [
                    modifier.get("name_en" if language == Language.EN.value else "name_ru", "")
                    for modifier in item.modifiers_snapshot
                ],
                "comment": item.comment,
            }
            for item in order.items
        ],
    }
