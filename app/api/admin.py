from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.domain import ACTIVE_ORDER_STATUSES, EventStatus
from app.models import (
    Category,
    Event,
    EventMenuItem,
    MenuItem,
    MenuItemModifier,
    Modifier,
    Order,
    SpecialRequest,
)
from app.schemas import (
    AvailabilityUpdate,
    CategoryCreate,
    CategoryUpdate,
    EventCreate,
    EventUpdate,
    MenuItemCreate,
    MenuItemUpdate,
    ModifierCreate,
    ModifierUpdate,
)
from app.services.orders import DomainError, get_active_event, list_event_menu

from .dependencies import require_admin
from .errors import domain_http_error

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
Session = Annotated[AsyncSession, Depends(get_session)]
AdminRole = Annotated[str, Depends(require_admin)]


def _event_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "code": event.code,
        "name": event.name,
        "starts_at": event.starts_at.isoformat(),
        "ends_at": event.ends_at.isoformat(),
        "status": event.status,
        "orders_enabled": event.orders_enabled,
        "max_items_per_order": event.max_items_per_order,
        "max_same_item": event.max_same_item,
        "menu_items_count": len(event.menu_items),
    }


def _category_dict(category: Category) -> dict[str, Any]:
    return {
        "id": category.id,
        "name_ru": category.name_ru,
        "name_en": category.name_en,
        "sort_order": category.sort_order,
        "is_active": category.is_active,
    }


def _modifier_dict(modifier: Modifier) -> dict[str, Any]:
    return {
        "id": modifier.id,
        "name_ru": modifier.name_ru,
        "name_en": modifier.name_en,
        "kind": modifier.kind,
        "aliases": modifier.aliases,
        "is_active": modifier.is_active,
    }


def _menu_item_dict(item: MenuItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "category_id": item.category_id,
        "name_ru": item.name_ru,
        "name_en": item.name_en,
        "description_ru": item.description_ru,
        "description_en": item.description_en,
        "ingredients_ru": item.ingredients_ru,
        "ingredients_en": item.ingredients_en,
        "image_url": item.image_url,
        "is_alcoholic": item.is_alcoholic,
        "is_archived": item.is_archived,
        "aliases": item.aliases,
        "taste_profile": item.taste_profile,
        "sort_order": item.sort_order,
        "modifier_ids": [link.modifier_id for link in item.allowed_modifiers],
        "events": [
            {"event_id": link.event_id, "is_available": link.is_available} for link in item.events
        ],
    }


async def _get_event_or_404(session: AsyncSession, event_id: int) -> Event:
    event = await session.scalar(
        select(Event).where(Event.id == event_id).options(selectinload(Event.menu_items))
    )
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


async def _validate_category(session: AsyncSession, category_id: int) -> None:
    if not await session.get(Category, category_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")


async def _load_modifiers(session: AsyncSession, modifier_ids: list[int]) -> list[Modifier]:
    unique_ids = list(dict.fromkeys(modifier_ids))
    if not unique_ids:
        return []
    modifiers = list(
        (await session.scalars(select(Modifier).where(Modifier.id.in_(unique_ids)))).all()
    )
    if len(modifiers) != len(unique_ids):
        found = {modifier.id for modifier in modifiers}
        missing = sorted(set(unique_ids) - found)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Modifiers not found: {missing}",
        )
    by_id = {modifier.id: modifier for modifier in modifiers}
    return [by_id[modifier_id] for modifier_id in unique_ids]


@router.get("/menu")
async def admin_menu(session: Session, _role: AdminRole, event_code: str | None = None) -> dict:
    try:
        event = await get_active_event(session, event_code)
        entries = await list_event_menu(session, event.id, only_available=False)
    except DomainError as exc:
        raise domain_http_error(exc) from exc
    return {
        "event": {
            "id": event.id,
            "name": event.name,
            "orders_enabled": event.orders_enabled,
        },
        "items": [
            {
                "id": entry.menu_item.id,
                "event_menu_item_id": entry.id,
                "name_ru": entry.menu_item.name_ru,
                "name_en": entry.menu_item.name_en,
                "category": entry.menu_item.category.name_ru,
                "is_available": entry.is_available,
            }
            for entry in entries
        ],
    }


@router.get("/events")
async def list_events(session: Session, _role: AdminRole) -> dict:
    events = list(
        (
            await session.scalars(
                select(Event)
                .options(selectinload(Event.menu_items))
                .order_by(Event.starts_at.desc(), Event.id.desc())
            )
        )
        .unique()
        .all()
    )
    return {"events": [_event_dict(event) for event in events]}


@router.get("/catalog")
async def get_catalog(session: Session, _role: AdminRole) -> dict:
    categories = list(
        (
            await session.scalars(select(Category).order_by(Category.sort_order, Category.name_ru))
        ).all()
    )
    modifiers = list(
        (await session.scalars(select(Modifier).order_by(Modifier.kind, Modifier.name_ru))).all()
    )
    items = list(
        (
            await session.scalars(
                select(MenuItem)
                .options(
                    selectinload(MenuItem.allowed_modifiers),
                    selectinload(MenuItem.events),
                )
                .order_by(MenuItem.sort_order, MenuItem.name_ru)
            )
        )
        .unique()
        .all()
    )
    return {
        "categories": [_category_dict(category) for category in categories],
        "modifiers": [_modifier_dict(modifier) for modifier in modifiers],
        "items": [_menu_item_dict(item) for item in items],
    }


@router.post("/events", status_code=status.HTTP_201_CREATED)
async def create_event(payload: EventCreate, session: Session, _role: AdminRole) -> dict:
    if await session.scalar(select(Event.id).where(Event.code == payload.code)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Event code already exists",
        )
    event = Event(
        **payload.model_dump(exclude={"include_catalog"}),
        status=EventStatus.DRAFT.value,
    )
    session.add(event)
    await session.flush()
    if payload.include_catalog:
        item_ids = list(
            (
                await session.scalars(select(MenuItem.id).where(MenuItem.is_archived.is_(False)))
            ).all()
        )
        session.add_all(
            [
                EventMenuItem(
                    event_id=event.id,
                    menu_item_id=item_id,
                    is_available=True,
                )
                for item_id in item_ids
            ]
        )
    await session.commit()
    return _event_dict(await _get_event_or_404(session, event.id))


@router.patch("/events/{event_id}")
async def update_event(
    event_id: int,
    payload: EventUpdate,
    session: Session,
    _role: AdminRole,
) -> dict:
    event = await _get_event_or_404(session, event_id)
    changes = payload.model_dump(exclude_unset=True)
    if "code" in changes:
        duplicate = await session.scalar(
            select(Event.id).where(Event.code == changes["code"], Event.id != event_id)
        )
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Event code already exists",
            )
    starts_at = changes.get("starts_at", event.starts_at)
    ends_at = changes.get("ends_at", event.ends_at)
    max_items = changes.get("max_items_per_order", event.max_items_per_order)
    max_same = changes.get("max_same_item", event.max_same_item)
    if ends_at <= starts_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ends_at must be later than starts_at",
        )
    if max_same > max_items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="max_same_item cannot exceed max_items_per_order",
        )
    for field, value in changes.items():
        setattr(event, field, value)
    await session.commit()
    return _event_dict(await _get_event_or_404(session, event.id))


@router.post("/events/{event_id}/activate")
async def activate_event(event_id: int, session: Session, _role: AdminRole) -> dict:
    # Stable lock order serializes event switches. Submissions lock their Event too.
    events = list(
        (
            await session.scalars(
                select(Event)
                .order_by(Event.id)
                .with_for_update(of=Event)
                .execution_options(populate_existing=True)
            )
        )
        .unique()
        .all()
    )
    event = next((row for row in events if row.id == event_id), None)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    for current in events:
        if current.status == EventStatus.ACTIVE.value and current.id != event.id:
            await _ensure_no_pending_work(session, current)
            current.status = EventStatus.CLOSED.value
            current.orders_enabled = False
    event.status = EventStatus.ACTIVE.value
    event.orders_enabled = True
    await session.commit()
    return _event_dict(await _get_event_or_404(session, event.id))


@router.post("/events/{event_id}/close")
async def close_event(event_id: int, session: Session, _role: AdminRole) -> dict:
    event = await session.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update(of=Event)
        .execution_options(populate_existing=True)
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    await _ensure_no_pending_work(session, event)
    event.status = EventStatus.CLOSED.value
    event.orders_enabled = False
    await session.commit()
    return _event_dict(await _get_event_or_404(session, event.id))


async def _ensure_no_pending_work(session: AsyncSession, event: Event) -> None:
    orders = await session.scalar(
        select(func.count(Order.id)).where(
            Order.event_id == event.id,
            Order.status.in_([value.value for value in ACTIVE_ORDER_STATUSES]),
        )
    )
    special = await session.scalar(
        select(func.count(SpecialRequest.id)).where(
            SpecialRequest.event_id == event.id,
            SpecialRequest.status.in_(["submitted", "accepted"]),
        )
    )
    if orders or special:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "pending_work",
                "event_name": event.name,
                "orders": orders,
                "special_requests": special,
                "message": "Сначала завершите заказы и особые запросы текущего мероприятия.",
            },
        )


@router.post("/categories", status_code=status.HTTP_201_CREATED)
async def create_category(payload: CategoryCreate, session: Session, _role: AdminRole) -> dict:
    category = Category(**payload.model_dump())
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return _category_dict(category)


@router.patch("/categories/{category_id}")
async def update_category(
    category_id: int,
    payload: CategoryUpdate,
    session: Session,
    _role: AdminRole,
) -> dict:
    category = await session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(category, field, value)
    await session.commit()
    await session.refresh(category)
    return _category_dict(category)


@router.post("/modifiers", status_code=status.HTTP_201_CREATED)
async def create_modifier(payload: ModifierCreate, session: Session, _role: AdminRole) -> dict:
    modifier = Modifier(**payload.model_dump())
    session.add(modifier)
    await session.commit()
    await session.refresh(modifier)
    return _modifier_dict(modifier)


@router.patch("/modifiers/{modifier_id}")
async def update_modifier(
    modifier_id: int,
    payload: ModifierUpdate,
    session: Session,
    _role: AdminRole,
) -> dict:
    modifier = await session.get(Modifier, modifier_id)
    if not modifier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Modifier not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(modifier, field, value)
    await session.commit()
    await session.refresh(modifier)
    return _modifier_dict(modifier)


@router.post("/menu-items", status_code=status.HTTP_201_CREATED)
async def create_menu_item(payload: MenuItemCreate, session: Session, _role: AdminRole) -> dict:
    await _validate_category(session, payload.category_id)
    modifiers = await _load_modifiers(session, payload.modifier_ids)
    data = payload.model_dump(exclude={"modifier_ids", "add_to_active_event"})
    item = MenuItem(**data)
    item.allowed_modifiers = [MenuItemModifier(modifier=modifier) for modifier in modifiers]
    session.add(item)
    await session.flush()
    if payload.add_to_active_event:
        active_event_id = await session.scalar(
            select(Event.id)
            .where(Event.status == EventStatus.ACTIVE.value)
            .order_by(Event.starts_at.desc())
        )
        if active_event_id:
            session.add(
                EventMenuItem(
                    event_id=active_event_id,
                    menu_item_id=item.id,
                    is_available=True,
                )
            )
    await session.commit()
    item = await session.scalar(
        select(MenuItem)
        .where(MenuItem.id == item.id)
        .options(selectinload(MenuItem.allowed_modifiers), selectinload(MenuItem.events))
    )
    return _menu_item_dict(item)


@router.patch("/menu-items/{menu_item_id}")
async def update_menu_item(
    menu_item_id: int,
    payload: MenuItemUpdate,
    session: Session,
    _role: AdminRole,
) -> dict:
    item = await session.scalar(
        select(MenuItem)
        .where(MenuItem.id == menu_item_id)
        .options(selectinload(MenuItem.allowed_modifiers), selectinload(MenuItem.events))
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu item not found")
    changes = payload.model_dump(exclude_unset=True, exclude={"modifier_ids"})
    if "category_id" in changes:
        await _validate_category(session, changes["category_id"])
    for field, value in changes.items():
        setattr(item, field, value)
    if payload.modifier_ids is not None:
        modifiers = await _load_modifiers(session, payload.modifier_ids)
        item.allowed_modifiers = [MenuItemModifier(modifier=modifier) for modifier in modifiers]
    await session.commit()
    item = await session.scalar(
        select(MenuItem)
        .where(MenuItem.id == menu_item_id)
        .options(selectinload(MenuItem.allowed_modifiers), selectinload(MenuItem.events))
    )
    return _menu_item_dict(item)


@router.patch("/events/{event_id}/menu/{menu_item_id}")
async def update_availability(
    event_id: int,
    menu_item_id: int,
    payload: AvailabilityUpdate,
    session: Session,
    _role: AdminRole,
) -> dict:
    await _get_event_or_404(session, event_id)
    if not await session.get(MenuItem, menu_item_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu item not found")
    entry = await session.scalar(
        select(EventMenuItem).where(
            EventMenuItem.event_id == event_id,
            EventMenuItem.menu_item_id == menu_item_id,
        )
    )
    if not entry:
        entry = EventMenuItem(event_id=event_id, menu_item_id=menu_item_id)
        session.add(entry)
    entry.is_available = payload.is_available
    await session.commit()
    return {"menu_item_id": menu_item_id, "is_available": entry.is_available}


@router.patch("/events/{event_id}/orders-enabled")
async def toggle_orders(
    event_id: int,
    payload: AvailabilityUpdate,
    session: Session,
    _role: AdminRole,
) -> dict:
    event = await _get_event_or_404(session, event_id)
    event.orders_enabled = payload.is_available
    await session.commit()
    return {"event_id": event.id, "orders_enabled": event.orders_enabled}
