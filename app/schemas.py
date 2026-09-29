from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain import Language, OrderStatus, SpecialRequestStatus


class UserUpsert(BaseModel):
    telegram_id: int
    username: str | None = None
    display_name: str = Field(min_length=1, max_length=160)
    language: Language = Language.RU


class CartItemCreate(BaseModel):
    menu_item_id: int
    quantity: int = Field(default=1, ge=1, le=10)
    modifier_ids: list[int] = Field(default_factory=list)
    comment: str = Field(default="", max_length=300)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=10)
    expected_quantity: int | None = Field(default=None, ge=1, le=10)


class OrderCreate(BaseModel):
    telegram_id: int
    comment: str = Field(default="", max_length=300)


class OrderEditRequest(BaseModel):
    telegram_id: int
    expected_version: int = Field(ge=1)


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    expected_version: int | None = None
    reason: str = Field(default="", max_length=300)


class OrderCollectionConfirm(BaseModel):
    expected_version: int = Field(ge=1)


class SpecialRequestStatusUpdate(BaseModel):
    status: SpecialRequestStatus
    note: str = Field(default="", max_length=300)


class EventCreate(BaseModel):
    code: str = Field(pattern=r"^[a-zA-Z0-9_-]{2,24}$")
    name: str = Field(min_length=2, max_length=160)
    starts_at: datetime
    ends_at: datetime
    orders_enabled: bool = True
    max_items_per_order: int = Field(default=5, ge=1, le=30)
    max_same_item: int = Field(default=3, ge=1, le=10)
    include_catalog: bool = True

    @model_validator(mode="after")
    def validate_period(self) -> "EventCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        if self.max_same_item > self.max_items_per_order:
            raise ValueError("max_same_item cannot exceed max_items_per_order")
        return self


class EventUpdate(BaseModel):
    code: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{2,24}$")
    name: str | None = Field(default=None, min_length=2, max_length=160)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    orders_enabled: bool | None = None
    max_items_per_order: int | None = Field(default=None, ge=1, le=30)
    max_same_item: int | None = Field(default=None, ge=1, le=10)


class CategoryCreate(BaseModel):
    name_ru: str = Field(min_length=1, max_length=100)
    name_en: str = Field(min_length=1, max_length=100)
    sort_order: int = 0
    is_active: bool = True


class CategoryUpdate(BaseModel):
    name_ru: str | None = Field(default=None, min_length=1, max_length=100)
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = None
    is_active: bool | None = None


class ModifierCreate(BaseModel):
    name_ru: str = Field(min_length=1, max_length=100)
    name_en: str = Field(min_length=1, max_length=100)
    kind: str = Field(default="extra", min_length=1, max_length=30)
    aliases: list[str] = Field(default_factory=list)
    is_active: bool = True


class ModifierUpdate(BaseModel):
    name_ru: str | None = Field(default=None, min_length=1, max_length=100)
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    kind: str | None = Field(default=None, min_length=1, max_length=30)
    aliases: list[str] | None = None
    is_active: bool | None = None


class MenuItemCreate(BaseModel):
    category_id: int
    name_ru: str = Field(min_length=1, max_length=120)
    name_en: str = Field(min_length=1, max_length=120)
    description_ru: str = ""
    description_en: str = ""
    ingredients_ru: str = ""
    ingredients_en: str = ""
    image_url: str | None = None
    is_alcoholic: bool = True
    aliases: list[str] = Field(default_factory=list)
    taste_profile: dict[str, Any] = Field(default_factory=dict)
    modifier_ids: list[int] = Field(default_factory=list)
    sort_order: int = 0
    add_to_active_event: bool = True


class MenuItemUpdate(BaseModel):
    category_id: int | None = None
    name_ru: str | None = Field(default=None, min_length=1, max_length=120)
    name_en: str | None = Field(default=None, min_length=1, max_length=120)
    description_ru: str | None = None
    description_en: str | None = None
    ingredients_ru: str | None = None
    ingredients_en: str | None = None
    image_url: str | None = None
    is_alcoholic: bool | None = None
    is_archived: bool | None = None
    aliases: list[str] | None = None
    taste_profile: dict[str, Any] | None = None
    modifier_ids: list[int] | None = None
    sort_order: int | None = None


class AvailabilityUpdate(BaseModel):
    is_available: bool


class SecretOfferCreate(BaseModel):
    event_id: int
    menu_item_id: int
    riddle_ru: str = Field(min_length=1, max_length=500)
    riddle_en: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=120)
    available_from: datetime
    available_until: datetime
    portions_total: int = Field(ge=1, le=10000)
    is_active: bool = True

    @field_validator("answer")
    @classmethod
    def nonblank_answer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Answer cannot be blank")
        return value.strip()

    @field_validator("available_from", "available_until")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Availability time must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_window(self) -> "SecretOfferCreate":
        if self.available_until <= self.available_from:
            raise ValueError("available_until must be later than available_from")
        return self


class SecretOfferUpdate(BaseModel):
    riddle_ru: str | None = Field(default=None, min_length=1, max_length=500)
    riddle_en: str | None = Field(default=None, min_length=1, max_length=500)
    answer: str | None = Field(default=None, min_length=1, max_length=120)
    available_from: datetime | None = None
    available_until: datetime | None = None
    portions_total: int | None = Field(default=None, ge=1, le=10000)
    is_active: bool | None = None

    @field_validator("answer")
    @classmethod
    def nonblank_answer(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Answer cannot be blank")
        return value.strip() if value is not None else None

    @field_validator("available_from", "available_until")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Availability time must include a timezone")
        return value.astimezone(UTC)
