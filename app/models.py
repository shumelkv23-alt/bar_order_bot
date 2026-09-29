from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.domain import EventStatus, Language, OrderStatus, Role, SpecialRequestStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(160))
    language: Mapped[str] = mapped_column(String(2), default=Language.RU.value)
    role: Mapped[str] = mapped_column(String(20), default=Role.GUEST.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    carts: Mapped[list[Cart]] = relationship(back_populates="user")
    orders: Mapped[list[Order]] = relationship(back_populates="user")


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default=EventStatus.DRAFT.value, index=True)
    orders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_items_per_order: Mapped[int] = mapped_column(Integer, default=5)
    max_same_item: Mapped[int] = mapped_column(Integer, default=3)
    last_order_number: Mapped[int] = mapped_column(Integer, default=0)

    menu_items: Mapped[list[EventMenuItem]] = relationship(
        back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )
    orders: Mapped[list[Order]] = relationship(back_populates="event")


class Category(TimestampMixin, Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ru: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    items: Mapped[list[MenuItem]] = relationship(back_populates="category", lazy="selectin")


class MenuItem(TimestampMixin, Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    name_ru: Mapped[str] = mapped_column(String(120))
    name_en: Mapped[str] = mapped_column(String(120))
    description_ru: Mapped[str] = mapped_column(Text, default="")
    description_en: Mapped[str] = mapped_column(Text, default="")
    ingredients_ru: Mapped[str] = mapped_column(Text, default="")
    ingredients_en: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(String(500))
    is_alcoholic: Mapped[bool] = mapped_column(Boolean, default=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    taste_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    category: Mapped[Category] = relationship(back_populates="items", lazy="joined")
    allowed_modifiers: Mapped[list[MenuItemModifier]] = relationship(
        back_populates="menu_item", cascade="all, delete-orphan", lazy="selectin"
    )
    events: Mapped[list[EventMenuItem]] = relationship(back_populates="menu_item")


class Modifier(TimestampMixin, Base):
    __tablename__ = "modifiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ru: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(30), default="extra")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    menu_items: Mapped[list[MenuItemModifier]] = relationship(back_populates="modifier")


class MenuItemModifier(Base):
    __tablename__ = "menu_item_modifiers"
    __table_args__ = (UniqueConstraint("menu_item_id", "modifier_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"))
    modifier_id: Mapped[int] = mapped_column(ForeignKey("modifiers.id", ondelete="CASCADE"))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    menu_item: Mapped[MenuItem] = relationship(back_populates="allowed_modifiers")
    modifier: Mapped[Modifier] = relationship(lazy="joined")


class EventMenuItem(Base):
    __tablename__ = "event_menu_items"
    __table_args__ = (UniqueConstraint("event_id", "menu_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    event: Mapped[Event] = relationship(back_populates="menu_items")
    menu_item: Mapped[MenuItem] = relationship(back_populates="events", lazy="joined")


class Cart(TimestampMixin, Base):
    __tablename__ = "carts"
    __table_args__ = (UniqueConstraint("user_id", "event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))

    user: Mapped[User] = relationship(back_populates="carts")
    items: Mapped[list[CartItem]] = relationship(
        back_populates="cart", cascade="all, delete-orphan", lazy="selectin"
    )


class CartItem(TimestampMixin, Base):
    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"))
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    selected_modifiers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    comment: Mapped[str] = mapped_column(String(300), default="")

    cart: Mapped[Cart] = relationship(back_populates="items")
    menu_item: Mapped[MenuItem] = relationship(lazy="joined")


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("event_id", "public_number"),
        Index(
            "uq_orders_event_user_idempotency",
            "event_id",
            "user_id",
            "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    public_number: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.SUBMITTED.value, index=True)
    comment: Mapped[str] = mapped_column(String(300), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(String(36))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preparing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped[Event] = relationship(back_populates="orders", lazy="joined")
    user: Mapped[User] = relationship(back_populates="orders", lazy="joined")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    history: Mapped[list[OrderStatusHistory]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )


class EventLeaderboardPreference(TimestampMixin, Base):
    __tablename__ = "event_leaderboard_preferences"
    __table_args__ = (UniqueConstraint("event_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    show_telegram_name: Mapped[bool] = mapped_column(Boolean, default=False)


class EventAchievement(Base):
    __tablename__ = "event_achievements"
    __table_args__ = (UniqueConstraint("event_id", "user_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SecretOffer(TimestampMixin, Base):
    __tablename__ = "secret_offers"
    __table_args__ = (UniqueConstraint("event_id", "menu_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"), index=True)
    riddle_ru: Mapped[str] = mapped_column(String(500))
    riddle_en: Mapped[str] = mapped_column(String(500))
    answer_salt: Mapped[str] = mapped_column(String(32))
    answer_hash: Mapped[str] = mapped_column(String(64))
    available_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    portions_total: Mapped[int] = mapped_column(Integer)
    portions_used: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    menu_item: Mapped[MenuItem] = relationship(lazy="joined")


class SecretUnlock(Base):
    __tablename__ = "secret_unlocks"
    __table_args__ = (UniqueConstraint("offer_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("secret_offers.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    menu_item_id: Mapped[int | None] = mapped_column(ForeignKey("menu_items.id"))
    secret_offer_id: Mapped[int | None] = mapped_column(ForeignKey("secret_offers.id"))
    name_ru_snapshot: Mapped[str] = mapped_column(String(120))
    name_en_snapshot: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(Integer)
    modifiers_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    comment: Mapped[str] = mapped_column(String(300), default="")

    order: Mapped[Order] = relationship(back_populates="items")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    order: Mapped[Order] = relationship(back_populates="history")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SpecialRequest(TimestampMixin, Base):
    __tablename__ = "special_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    suggested_menu_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("menu_items.id"), nullable=True
    )
    request_text: Mapped[str] = mapped_column(String(300))
    source_transcript: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(
        String(20), default=SpecialRequestStatus.SUBMITTED.value, index=True
    )
    bartender_note: Mapped[str] = mapped_column(String(300), default="")

    event: Mapped[Event] = relationship(lazy="joined")
    user: Mapped[User] = relationship(lazy="joined")
    suggested_menu_item: Mapped[MenuItem | None] = relationship(lazy="joined")
