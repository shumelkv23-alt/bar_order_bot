from __future__ import annotations

import logging
from html import escape
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.keyboards import (
    CATALOG_PAGE_SIZE,
    active_order_keyboard,
    cart_keyboard,
    categories_keyboard,
    item_draft_keyboard,
    items_keyboard,
    language_keyboard,
    main_menu_keyboard,
    voice_review_keyboard,
    waiter_keyboard,
)
from app.config import get_settings
from app.db import async_session_factory
from app.domain import Language, OrderStatus
from app.models import User
from app.services.assistant import (
    AssistantMenuItem,
    AssistantResponse,
    AssistantUnavailable,
    MenuAssistantService,
    fallback_assistant_response,
)
from app.services.orders import (
    DomainError,
    add_to_cart,
    cart_to_dict,
    create_special_request,
    get_active_event,
    get_cart,
    get_menu_entry,
    get_order,
    get_user_active_order,
    list_event_menu,
    order_to_dict,
    remove_cart_item,
    reopen_order_for_edit,
    set_user_language,
    submit_cart,
    transition_order,
    update_cart_item_quantity,
    upsert_user,
)
from app.services.parser import ParseCandidate, analyze_order_text, parse_order_text
from app.services.speech import SpeechRecognitionUnavailable, SpeechToTextService

router = Router(name="guest-bot")
logger = logging.getLogger(__name__)

ORDER_STATUS_LABELS = {
    Language.RU.value: {
        OrderStatus.SUBMITTED.value: "новый",
        OrderStatus.ACCEPTED.value: "принят",
        OrderStatus.PREPARING.value: "готовится",
        OrderStatus.READY.value: "готов",
        OrderStatus.COMPLETED.value: "выдан",
        OrderStatus.CANCELLED.value: "отменён",
        OrderStatus.REJECTED.value: "отклонён",
    },
    Language.EN.value: {
        OrderStatus.SUBMITTED.value: "submitted",
        OrderStatus.ACCEPTED.value: "accepted",
        OrderStatus.PREPARING.value: "being prepared",
        OrderStatus.READY.value: "ready",
        OrderStatus.COMPLETED.value: "collected",
        OrderStatus.CANCELLED.value: "cancelled",
        OrderStatus.REJECTED.value: "declined",
    },
}


class ItemDraft(StatesGroup):
    choosing = State()


class TextOrder(StatesGroup):
    waiting = State()


class Recommendation(StatesGroup):
    waiting = State()


class VoiceOrder(StatesGroup):
    reviewing = State()


class Waiter(StatesGroup):
    waiting = State()


async def _user(session, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def _resume_waiter(state: FSMContext) -> None:
    """Keep conversation and pending review when navigating between screens."""
    data = await state.get_data()
    retained = {
        key: data[key]
        for key in (
            "assistant_history",
            "transcript",
            "language",
            "event_id",
            "items",
            "unmatched",
            "assistant_reply",
        )
        if key in data
    }
    await state.set_data(retained)
    await state.set_state(
        VoiceOrder.reviewing
        if retained.get("items") or retained.get("unmatched")
        else Waiter.waiting
    )


def _main_text(language: str, event_name: str) -> str:
    if language == Language.EN.value:
        return (
            f"<b>{escape(event_name)}</b>\n\n"
            "I'm your waiter. Tell me what you'd like — by text or voice. "
            "I'll help you choose and ask you to confirm your order."
        )
    return (
        f"<b>{escape(event_name)}</b>\n\n"
        "Я ваш официант. Напишите, чего хочется, или отправьте голосовое. "
        "Помогу выбрать и попрошу подтвердить заказ."
    )


async def _show_main(target: Message, language: str) -> None:
    async with async_session_factory() as session:
        try:
            event = await get_active_event(session)
            text = _main_text(language, event.name)
        except DomainError:
            text = (
                "No active event."
                if language == Language.EN.value
                else "Сейчас нет активного мероприятия."
            )
    await target.edit_text(text, reply_markup=main_menu_keyboard(language))


@router.message(Command("start"))
async def start(message: Message, state: FSMContext) -> None:
    async with async_session_factory() as session:
        existing = await _user(session, message.from_user.id)
        language = (
            existing.language
            if existing
            else (
                Language.EN.value
                if (message.from_user.language_code or "").lower().startswith("en")
                else Language.RU.value
            )
        )
        await upsert_user(
            session,
            telegram_id=message.from_user.id,
            display_name=message.from_user.full_name,
            username=message.from_user.username,
            language=language,
        )
        try:
            event = await get_active_event(session)
            text = _main_text(language, event.name)
        except DomainError:
            text = (
                "I'm your waiter. There is no active event yet."
                if language == "en"
                else "Я ваш официант. Сейчас нет активного мероприятия."
            )
    await state.clear()
    await state.set_state(Waiter.waiting)
    await message.answer(text, reply_markup=waiter_keyboard(language))


@router.message(Command("language"))
async def language_command(message: Message) -> None:
    await message.answer("Русский / English", reply_markup=language_keyboard())


@router.callback_query(F.data.startswith("lang:"))
async def choose_language(callback: CallbackQuery, state: FSMContext) -> None:
    language = callback.data.split(":", 1)[1]
    async with async_session_factory() as session:
        try:
            await set_user_language(session, callback.from_user.id, language)
        except DomainError:
            await upsert_user(
                session,
                callback.from_user.id,
                callback.from_user.full_name,
                callback.from_user.username,
                language,
            )
    await _resume_waiter(state)
    await callback.answer()
    await _show_main(callback.message, language)


@router.callback_query(F.data == "main")
async def main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await _resume_waiter(state)
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
    language = user.language if user else Language.RU.value
    await callback.answer()
    await _show_main(callback.message, language)


@router.callback_query(F.data == "settings")
async def settings(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(
        "Выберите язык / Choose language",
        reply_markup=language_keyboard(),
    )


@router.callback_query(F.data == "waiter")
async def waiter_start(callback: CallbackQuery, state: FSMContext) -> None:
    await main_menu(callback, state)


@router.callback_query(F.data == "menu")
async def show_categories(callback: CallbackQuery, state: FSMContext) -> None:
    await _resume_waiter(state)
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        language = user.language if user else Language.RU.value
        try:
            event = await get_active_event(session)
            entries = await list_event_menu(session, event.id)
        except DomainError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        categories = []
        seen = set()
        for entry in entries:
            category = entry.menu_item.category
            if category.id not in seen:
                seen.add(category.id)
                categories.append(category)
        categories.sort(key=lambda category: (category.sort_order, category.id))
    title = (
        "<b>Catalog</b> · choose a section"
        if language == Language.EN.value
        else "<b>Каталог</b> · выберите раздел"
    )
    await callback.answer()
    await callback.message.edit_text(title, reply_markup=categories_keyboard(categories, language))


@router.callback_query(F.data.startswith("cat:"))
async def show_items(callback: CallbackQuery, state: FSMContext) -> None:
    await _resume_waiter(state)
    callback_parts = callback.data.split(":")
    category_id = int(callback_parts[1])
    requested_page = int(callback_parts[2]) if len(callback_parts) > 2 else 0
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        language = user.language if user else Language.RU.value
        event = await get_active_event(session)
        entries = await list_event_menu(session, event.id, category_id=category_id)
        items = [entry.menu_item for entry in entries]
    total_pages = max(1, (len(items) + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
    page = min(max(0, requested_page), total_pages - 1)
    page_items = items[page * CATALOG_PAGE_SIZE : (page + 1) * CATALOG_PAGE_SIZE]
    category = items[0].category if items else None
    if category:
        category_name = category.name_en if language == Language.EN.value else category.name_ru
    else:
        category_name = "Menu" if language == Language.EN.value else "Меню"
    page_label = f" · {page + 1}/{total_pages}" if total_pages > 1 else ""
    text = (
        f"<b>{escape(category_name)}</b>{page_label}\nChoose an item — details open next."
        if language == Language.EN.value
        else f"<b>{escape(category_name)}</b>{page_label}\nНажмите позицию — покажу описание."
    )
    await callback.answer()
    await callback.message.edit_text(
        text,
        reply_markup=items_keyboard(
            page_items,
            language,
            category_id=category_id,
            page=page,
            total_pages=total_pages,
        ),
    )


async def _render_item(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    item_id = data.get("item_id")
    if not item_id:
        await callback.answer("Open the menu again", show_alert=True)
        return
    selected = set(data.get("selected", []))
    quantity = int(data.get("quantity", 1))
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        language = user.language if user else Language.RU.value
        event = await get_active_event(session)
        entry = await get_menu_entry(session, event.id, item_id)
        item = entry.menu_item
        max_quantity = event.max_same_item
    await state.update_data(max_quantity=max_quantity)
    name = item.name_en if language == Language.EN.value else item.name_ru
    description = item.description_en if language == Language.EN.value else item.description_ru
    text = f"<b>{name}</b>\n\n{description}"
    await callback.message.edit_text(
        text,
        reply_markup=item_draft_keyboard(
            item,
            selected,
            quantity,
            language,
            max_quantity=max_quantity,
            category_page=int(data.get("category_page", 0)),
        ),
    )


@router.callback_query(F.data.startswith("item:"))
async def open_item(callback: CallbackQuery, state: FSMContext) -> None:
    callback_parts = callback.data.split(":")
    item_id = int(callback_parts[1])
    category_page = int(callback_parts[2]) if len(callback_parts) > 2 else 0
    await state.set_state(ItemDraft.choosing)
    await state.update_data(
        {
            "item_id": item_id,
            "selected": [],
            "quantity": 1,
            "category_page": category_page,
        }
    )
    await callback.answer()
    await _render_item(callback, state)


@router.callback_query(ItemDraft.choosing, F.data.startswith("mod:"))
async def toggle_modifier(callback: CallbackQuery, state: FSMContext) -> None:
    modifier_id = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    selected = set(data.get("selected", []))
    if modifier_id in selected:
        selected.remove(modifier_id)
    else:
        async with async_session_factory() as session:
            event = await get_active_event(session)
            entry = await get_menu_entry(session, event.id, int(data["item_id"]))
            modifier_kinds = {
                link.modifier.id: link.modifier.kind for link in entry.menu_item.allowed_modifiers
            }
        target_kind = modifier_kinds.get(modifier_id)
        if target_kind in {"ice", "variant"}:
            selected = {
                selected_id
                for selected_id in selected
                if modifier_kinds.get(selected_id) != target_kind
            }
        selected.add(modifier_id)
    await state.update_data(selected=sorted(selected))
    await callback.answer()
    await _render_item(callback, state)


@router.callback_query(ItemDraft.choosing, F.data.startswith("qty:"))
async def change_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    direction = callback.data.split(":", 1)[1]
    data = await state.get_data()
    quantity = int(data.get("quantity", 1))
    max_quantity = int(data.get("max_quantity", 3))
    new_quantity = min(max_quantity, quantity + 1) if direction == "plus" else max(1, quantity - 1)
    if new_quantity == quantity:
        await callback.answer()
        return
    await state.update_data(quantity=new_quantity)
    await callback.answer()
    await _render_item(callback, state)


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(ItemDraft.choosing, F.data == "draft:add")
async def add_draft_to_cart(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user:
            await callback.answer("Use /start first", show_alert=True)
            return
        try:
            event = await get_active_event(session)
            await add_to_cart(
                session,
                user.id,
                event.id,
                int(data["item_id"]),
                int(data.get("quantity", 1)),
                list(data.get("selected", [])),
            )
        except (DomainError, KeyError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        language = user.language
    message = "Added to cart" if language == Language.EN.value else "Добавлено в корзину"
    await show_cart(callback, state, answer_text=message)


@router.callback_query(F.data == "open_order")
async def open_current_order(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("items") or data.get("unmatched"):
        await state.set_state(VoiceOrder.reviewing)
        await callback.answer()
        await _show_voice_review(callback.message, state)
        return
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user:
            await callback.answer("Use /start first", show_alert=True)
            return
        try:
            event = await get_active_event(session)
            cart = await get_cart(session, user.id, event.id)
            has_cart = bool(cart.items)
            order = await get_user_active_order(session, user.id, event.id)
        except DomainError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    if has_cart or not order:
        await show_cart(callback, state)
    else:
        await my_order(callback, state)


@router.callback_query(F.data == "cart")
async def show_cart(
    callback: CallbackQuery,
    state: FSMContext,
    answer_text: str | None = None,
) -> None:
    await _resume_waiter(state)
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user:
            await callback.answer("Use /start first", show_alert=True)
            return
        event = await get_active_event(session)
        cart = await get_cart(session, user.id, event.id)
        payload = cart_to_dict(cart, user.language)
        active_order = await get_user_active_order(session, user.id, event.id)
    if payload["items"]:
        lines = []
        for item in payload["items"]:
            modifiers = f" ({', '.join(item['modifiers'])})" if item["modifiers"] else ""
            lines.append(f"• {item['quantity']} × {item['name']}{modifiers}")
        title = "<b>Your cart</b>" if user.language == Language.EN.value else "<b>Ваша корзина</b>"
        text = title + "\n\n" + "\n".join(lines)
    else:
        text = "Your cart is empty." if user.language == Language.EN.value else "Корзина пуста."
    await callback.answer(answer_text)
    await callback.message.edit_text(
        text,
        reply_markup=cart_keyboard(
            user.language,
            payload["items"],
            max_quantity=event.max_same_item,
            has_active_order=active_order is not None,
        ),
    )


@router.callback_query(F.data.startswith("cartqty:"))
async def change_cart_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    _, item_id_raw, expected_raw, direction = callback.data.split(":")
    expected_quantity = int(expected_raw)
    quantity = expected_quantity + (1 if direction == "plus" else -1)
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user:
            await callback.answer("Use /start first", show_alert=True)
            return
        try:
            await update_cart_item_quantity(
                session,
                user.id,
                int(item_id_raw),
                quantity,
                expected_quantity=expected_quantity,
            )
        except DomainError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    await show_cart(callback, state)


@router.callback_query(F.data.startswith("cartdel:"))
async def delete_cart_position(callback: CallbackQuery, state: FSMContext) -> None:
    _, item_id_raw, expected_raw = callback.data.split(":")
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user:
            await callback.answer("Use /start first", show_alert=True)
            return
        try:
            await remove_cart_item(
                session,
                user.id,
                int(item_id_raw),
                expected_quantity=int(expected_raw),
            )
        except DomainError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    await show_cart(callback, state)


@router.callback_query(F.data == "checkout")
async def checkout(callback: CallbackQuery, state: FSMContext) -> None:
    await _resume_waiter(state)
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user:
            await callback.answer("Use /start first", show_alert=True)
            return
        try:
            event = await get_active_event(session)
            order = await submit_cart(session, user.id, event.id)
        except DomainError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    if user.language == Language.EN.value:
        text = (
            f"Order <b>{order.public_number}</b> has been sent. "
            "We will notify you when it is ready."
        )
    else:
        text = f"Заказ <b>{order.public_number}</b> отправлен. Мы сообщим, когда он будет готов."
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(user.language))


@router.callback_query(F.data == "my_order")
async def my_order(callback: CallbackQuery, state: FSMContext) -> None:
    await _resume_waiter(state)
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user:
            await callback.answer("Use /start first", show_alert=True)
            return
        event = await get_active_event(session)
        order = await get_user_active_order(session, user.id, event.id)
        if not order:
            text = (
                "No active order."
                if user.language == Language.EN.value
                else "Активного заказа нет."
            )
            keyboard = main_menu_keyboard(user.language)
        else:
            payload = order_to_dict(order, user.language)
            lines = [f"• {item['quantity']} × {item['name']}" for item in payload["items"]]
            status_label = ORDER_STATUS_LABELS[user.language].get(order.status, order.status)
            status_title = "Status" if user.language == Language.EN.value else "Статус"
            text = f"<b>{order.public_number}</b>\n{status_title}: {status_label}\n\n" + "\n".join(
                lines
            )
            keyboard = active_order_keyboard(
                user.language,
                order.id,
                order.version,
                order.status == OrderStatus.SUBMITTED.value,
            )
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("cancel_order:"))
async def cancel_active_order(callback: CallbackQuery, state: FSMContext) -> None:
    _, order_id_raw, expected_version_raw = callback.data.split(":")
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user:
            await callback.answer("Use /start first", show_alert=True)
            return
        try:
            order = await get_order(session, int(order_id_raw))
        except DomainError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        if order.user_id != user.id:
            await callback.answer("Order not found", show_alert=True)
            return
        try:
            await transition_order(
                session,
                order.id,
                OrderStatus.CANCELLED,
                actor=f"guest:{callback.from_user.id}",
                expected_version=int(expected_version_raw),
            )
        except DomainError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    await callback.answer("Cancelled" if user.language == Language.EN.value else "Заказ отменён")
    await _resume_waiter(state)
    await _show_main(callback.message, user.language)


@router.callback_query(F.data.startswith("edit_order:"))
async def edit_active_order(callback: CallbackQuery, state: FSMContext) -> None:
    _, order_id_raw, expected_version_raw = callback.data.split(":")
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user:
            await callback.answer("Use /start first", show_alert=True)
            return
        try:
            await reopen_order_for_edit(
                session,
                int(order_id_raw),
                user.id,
                expected_version=int(expected_version_raw),
                actor=f"guest:{callback.from_user.id}",
            )
        except DomainError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        language = user.language

    await show_cart(
        callback,
        state,
        answer_text=(
            "Order returned to cart"
            if language == Language.EN.value
            else "Заказ возвращён в корзину"
        ),
    )


@router.callback_query(F.data == "voice_help")
async def voice_help(callback: CallbackQuery, state: FSMContext) -> None:
    await _resume_waiter(state)
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
    language = user.language if user else Language.RU.value
    text = (
        "Send a voice message, for example: ‘Two mojitos, one alcohol-free’."
        if language == Language.EN.value
        else "Отправьте голосовое сообщение, например: «Два мохито, один безалкогольный»."
    )
    await callback.answer()
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(language))


@router.callback_query(F.data == "text_order")
async def text_order_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await _resume_waiter(state)
    await callback.answer()
    await callback.message.edit_text(
        "Напишите заказ одним сообщением / Type your order in one message"
    )


async def _parse_and_add(message: Message, text: str) -> str:
    async with async_session_factory() as session:
        user = await _user(session, message.from_user.id)
        if not user:
            return "Use /start first"
        event = await get_active_event(session)
        entries = await list_event_menu(session, event.id)
        candidates = [
            ParseCandidate(
                id=entry.menu_item.id,
                name=(
                    entry.menu_item.name_en
                    if user.language == Language.EN.value
                    else entry.menu_item.name_ru
                ),
                aliases=entry.menu_item.aliases,
                modifiers={
                    link.modifier.id: [
                        link.modifier.name_ru,
                        link.modifier.name_en,
                        *link.modifier.aliases,
                    ]
                    for link in entry.menu_item.allowed_modifiers
                },
            )
            for entry in entries
        ]
        parsed = parse_order_text(text, candidates)
        if not parsed:
            return (
                "I could not match the order to the menu. Please use the catalog."
                if user.language == Language.EN.value
                else "Не удалось сопоставить заказ с меню. Попробуйте каталог."
            )
        added = []
        for item in parsed:
            try:
                await add_to_cart(
                    session,
                    user.id,
                    event.id,
                    item.menu_item_id,
                    item.quantity,
                    item.modifier_ids,
                )
                added.append(f"{item.quantity} × {item.name}")
            except DomainError:
                continue
        if not added:
            return "Не удалось добавить позиции в корзину."
        prefix = (
            "Recognized and added:\n"
            if user.language == Language.EN.value
            else "Распознано и добавлено:\n"
        )
        return prefix + "\n".join(f"• {line}" for line in added)


def _voice_candidates(entries: list, language: str) -> list[ParseCandidate]:
    return [
        ParseCandidate(
            id=entry.menu_item.id,
            name=(
                entry.menu_item.name_en
                if language == Language.EN.value
                else entry.menu_item.name_ru
            ),
            aliases=entry.menu_item.aliases,
            modifiers={
                link.modifier.id: [
                    link.modifier.name_ru,
                    link.modifier.name_en,
                    *link.modifier.aliases,
                ]
                for link in entry.menu_item.allowed_modifiers
            },
        )
        for entry in entries
    ]


async def _voice_context(telegram_id: int) -> dict:
    async with async_session_factory() as session:
        user = await _user(session, telegram_id)
        if not user:
            raise DomainError("Use /start first")
        event = await get_active_event(session)
        entries = await list_event_menu(session, event.id)
        cart = await get_cart(session, user.id, event.id)
        current_cart = cart_to_dict(cart, user.language)["items"]
        terms: list[str] = []
        modifier_names: dict[int, dict[int, str]] = {}
        assistant_menu: list[AssistantMenuItem] = []
        for entry in entries:
            item = entry.menu_item
            terms.extend([item.name_ru, item.name_en, *item.aliases])
            modifier_names[item.id] = {}
            modifier_aliases: dict[int, list[str]] = {}
            for link in item.allowed_modifiers:
                modifier = link.modifier
                terms.extend([modifier.name_ru, modifier.name_en, *modifier.aliases])
                modifier_names[item.id][modifier.id] = (
                    modifier.name_en if user.language == Language.EN.value else modifier.name_ru
                )
                modifier_aliases[modifier.id] = [
                    modifier.name_ru,
                    modifier.name_en,
                    *modifier.aliases,
                ]
            assistant_menu.append(
                AssistantMenuItem(
                    id=item.id,
                    name=(item.name_en if user.language == Language.EN.value else item.name_ru),
                    name_ru=item.name_ru,
                    name_en=item.name_en,
                    description=(
                        item.description_en
                        if user.language == Language.EN.value
                        else item.description_ru
                    ),
                    ingredients=(
                        item.ingredients_en
                        if user.language == Language.EN.value
                        else item.ingredients_ru
                    ),
                    aliases=item.aliases,
                    taste_profile=item.taste_profile,
                    is_alcoholic=item.is_alcoholic,
                    modifiers=modifier_names[item.id],
                    modifier_aliases=modifier_aliases,
                )
            )
        vocabulary = ", ".join(dict.fromkeys(term.strip() for term in terms if term.strip()))
        return {
            "language": user.language,
            "event_id": event.id,
            "max_same_item": event.max_same_item,
            "candidates": _voice_candidates(entries, user.language),
            "modifier_names": modifier_names,
            "assistant_menu": assistant_menu,
            "current_cart": current_cart,
            "prompt": f"Bar menu vocabulary: {vocabulary}",
        }


async def _assistant_response(
    text: str,
    context: dict,
    history: list[dict[str, str]] | None = None,
    current_draft: dict | None = None,
) -> AssistantResponse:
    settings = get_settings()
    if settings.assistant_enabled:
        service = MenuAssistantService(
            settings.effective_assistant_api_key,
            settings.assistant_model,
            settings.effective_assistant_base_url,
            timeout_seconds=settings.assistant_timeout_seconds,
        )
        try:
            return await service.respond(
                text,
                language=context["language"],
                menu=context["assistant_menu"],
                max_same_item=context["max_same_item"],
                history=history,
                current_cart=context.get("current_cart", []),
                current_draft=current_draft,
            )
        except AssistantUnavailable as exc:
            logger.info("AI waiter fallback used: %s", exc)
    return fallback_assistant_response(
        text,
        language=context["language"],
        menu=context["assistant_menu"],
        max_same_item=context["max_same_item"],
    )


def _updated_assistant_history(
    history: list[dict[str, str]] | None,
    user_text: str,
    assistant_text: str,
) -> list[dict[str, str]]:
    updated = list(history or [])[-8:]
    updated.extend(
        [
            {"role": "user", "content": user_text[:1000]},
            {"role": "assistant", "content": assistant_text[:1000]},
        ]
    )
    return updated[-10:]


def _current_draft(data: dict) -> dict:
    return {
        "items": [
            {
                "menu_item_id": item["menu_item_id"],
                "name": item["name"],
                "quantity": item["quantity"],
                "modifier_ids": item.get("modifier_ids", []),
                "modifier_names": item.get("modifier_names", []),
            }
            for item in data.get("items", [])
        ],
        "unmatched": [
            {"text": row["text"], "quantity": row["quantity"]} for row in data.get("unmatched", [])
        ],
    }


def _assistant_review_data(
    result: AssistantResponse,
    context: dict,
) -> tuple[list[dict], list[dict]]:
    menu_by_id = {item.id: item for item in context["assistant_menu"]}
    items: list[dict] = []
    for row in result.items:
        item = menu_by_id.get(row.menu_item_id)
        if not item:
            continue
        items.append(
            {
                "menu_item_id": item.id,
                "name": item.name,
                "quantity": row.quantity,
                "modifier_ids": row.modifier_ids,
                "modifier_names": [
                    item.modifiers[modifier_id]
                    for modifier_id in row.modifier_ids
                    if modifier_id in item.modifiers
                ],
                "confidence": row.confidence,
                "source_text": row.source_text,
            }
        )

    unmatched: list[dict] = []
    for row in result.unmatched:
        suggestions: list[dict] = []
        suggestion_analysis = analyze_order_text(row.text, context["candidates"])
        if suggestion_analysis.items:
            suggestions = [
                {
                    "menu_item_id": match.menu_item_id,
                    "name": match.name,
                    "confidence": match.confidence,
                }
                for match in suggestion_analysis.items[:2]
            ]
        elif suggestion_analysis.unmatched:
            suggestions = [
                {
                    "menu_item_id": suggestion.menu_item_id,
                    "name": suggestion.name,
                    "confidence": suggestion.confidence,
                }
                for suggestion in suggestion_analysis.unmatched[0].suggestions[:2]
            ]
        unmatched.append({"text": row.text, "quantity": row.quantity, "suggestions": suggestions})
    return items, unmatched


def _assistant_recommendations(result: AssistantResponse, context: dict) -> list[dict]:
    menu_by_id = {item.id: item for item in context["assistant_menu"]}
    return [
        {"id": item.id, "name": item.name}
        for item_id in result.recommendation_ids
        if (item := menu_by_id.get(item_id))
    ]


async def _present_assistant_result(
    message: Message,
    state: FSMContext,
    text: str,
    context: dict,
    result: AssistantResponse,
    *,
    edit: bool = False,
    keep_existing_draft_on_question: bool = False,
) -> None:
    data = await state.get_data()
    recommendations = _assistant_recommendations(result, context)
    reply = result.reply
    if recommendations:
        names = "\n".join(
            f"{index}. {item['name']}" for index, item in enumerate(recommendations, 1)
        )
        reply = f"{reply}\n\n{names}".strip()
    history = _updated_assistant_history(
        data.get("assistant_history", []),
        text,
        reply,
    )
    items, unmatched = _assistant_review_data(result, context)
    if items or unmatched:
        await state.set_state(VoiceOrder.reviewing)
        await state.update_data(
            transcript=text,
            language=context["language"],
            event_id=context["event_id"],
            items=items,
            unmatched=unmatched,
            assistant_reply=reply,
            assistant_history=history,
        )
        await _show_voice_review(message, state)
        return

    if keep_existing_draft_on_question and (data.get("items") or data.get("unmatched")):
        await state.set_state(VoiceOrder.reviewing)
        await state.update_data(
            assistant_reply=reply,
            assistant_history=history,
        )
        await _show_voice_review(message, state)
        return

    await state.set_state(Waiter.waiting)
    await state.set_data({"assistant_history": history})
    response_text = escape(reply) or (
        "Уточните, пожалуйста, что вы хотите заказать."
        if context["language"] == Language.RU.value
        else "Please tell me a little more about what you would like."
    )
    keyboard = waiter_keyboard(context["language"])
    if edit:
        await message.edit_text(response_text, reply_markup=keyboard)
    else:
        await message.answer(response_text, reply_markup=keyboard)


def _voice_review_text(
    transcript: str,
    items: list[dict],
    unmatched: list[dict],
    language: str,
    assistant_reply: str = "",
) -> str:
    lines = []
    if assistant_reply:
        lines.append(escape(assistant_reply))
    lines.append(
        f"<b>Your request:</b> <i>{escape(transcript)}</i>"
        if language == Language.EN.value
        else f"<b>Ваш запрос:</b> <i>{escape(transcript)}</i>"
    )
    if items:
        lines.append(
            "\n<b>Matched:</b>" if language == Language.EN.value else "\n<b>Нашёл в меню:</b>"
        )
        for item in items:
            modifiers = ", ".join(escape(name) for name in item.get("modifier_names", []))
            uncertain = item["confidence"] < 0.9
            suffix = f" ({modifiers})" if modifiers else ""
            if uncertain:
                suffix += " · please verify" if language == Language.EN.value else " · проверьте"
            lines.append(f"• {item['quantity']} × {escape(item['name'])}{suffix}")
    if unmatched:
        lines.append(
            "\n<b>Not on the menu:</b>"
            if language == Language.EN.value
            else "\n<b>Не нашёл в меню:</b>"
        )
        for clause in unmatched:
            suggestions = ", ".join(
                escape(suggestion["name"]) for suggestion in clause.get("suggestions", [])
            )
            line = f"• {clause['quantity']} × {escape(clause['text'])}"
            if suggestions:
                line += (
                    f"\n  Similar: {suggestions}"
                    if language == Language.EN.value
                    else f"\n  Похожие: {suggestions}"
                )
            lines.append(line)
        lines.append(
            "I can send unknown items to the bartender as a special request."
            if language == Language.EN.value
            else "Неизвестные позиции можно передать бармену как особый запрос."
        )
    lines.append(
        "\nYou can also type a correction, for example: “make the second one without ice”."
        if language == Language.EN.value
        else "\nМожно написать исправление, например: «второй без льда»."
    )
    return "\n".join(lines)


async def _show_voice_review(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    language = data.get("language", Language.RU.value)
    await message.edit_text(
        _voice_review_text(
            data.get("transcript", ""),
            data.get("items", []),
            data.get("unmatched", []),
            language,
            data.get("assistant_reply", ""),
        ),
        reply_markup=voice_review_keyboard(
            language,
            data.get("items", []),
            data.get("unmatched", []),
        ),
    )


@router.message(TextOrder.waiting, F.text)
async def receive_text_order(message: Message, state: FSMContext) -> None:
    await waiter_message(message, state)


@router.message(Waiter.waiting, F.text)
async def waiter_message(message: Message, state: FSMContext) -> None:
    status = await message.answer("Секунду, сверяюсь с меню... / Checking the menu...")
    try:
        context = await _voice_context(message.from_user.id)
        data = await state.get_data()
        result = await _assistant_response(
            message.text,
            context,
            history=data.get("assistant_history", []),
            current_draft=_current_draft(data),
        )
        await _present_assistant_result(
            status,
            state,
            message.text,
            context,
            result,
            edit=True,
            keep_existing_draft_on_question=True,
        )
    except DomainError as exc:
        await _resume_waiter(state)
        await status.edit_text(str(exc))


@router.message(VoiceOrder.reviewing, F.text)
async def revise_voice_order(message: Message, state: FSMContext) -> None:
    status = await message.answer("Уточняю заказ... / Updating your order...")
    try:
        context = await _voice_context(message.from_user.id)
        data = await state.get_data()
        result = await _assistant_response(
            message.text,
            context,
            history=data.get("assistant_history", []),
            current_draft=_current_draft(data),
        )
        await _present_assistant_result(
            status,
            state,
            message.text,
            context,
            result,
            edit=True,
            keep_existing_draft_on_question=True,
        )
    except DomainError as exc:
        await _resume_waiter(state)
        await status.edit_text(str(exc))


@router.message(F.voice)
async def receive_voice_order(message: Message, bot: Bot, state: FSMContext) -> None:
    status = await message.answer("Распознаю заказ... / Recognizing your order...")
    try:
        context = await _voice_context(message.from_user.id)
    except DomainError as exc:
        await status.edit_text(str(exc))
        return
    file = await bot.get_file(message.voice.file_id)
    buffer = BytesIO()
    await bot.download_file(file.file_path, buffer)
    settings = get_settings()
    service = SpeechToTextService(
        settings.openai_api_key,
        settings.openai_transcription_model,
        settings.openai_base_url,
    )
    try:
        transcription = await service.transcribe(
            buffer.getvalue(),
            language=context["language"],
            prompt=context["prompt"],
        )
        data = await state.get_data()
        result = await _assistant_response(
            transcription,
            context,
            history=data.get("assistant_history", []),
            current_draft=(
                _current_draft(data) if data.get("items") or data.get("unmatched") else None
            ),
        )
        await _present_assistant_result(
            status,
            state,
            transcription,
            context,
            result,
            edit=True,
            keep_existing_draft_on_question=bool(data.get("items") or data.get("unmatched")),
        )
    except SpeechRecognitionUnavailable as exc:
        await _resume_waiter(state)
        await status.edit_text(
            str(exc),
            reply_markup=main_menu_keyboard(context["language"]),
        )


@router.callback_query(VoiceOrder.reviewing, F.data.startswith("voice_suggest:"))
async def choose_voice_suggestion(callback: CallbackQuery, state: FSMContext) -> None:
    _, clause_index_raw, menu_item_id_raw = callback.data.split(":")
    clause_index = int(clause_index_raw)
    menu_item_id = int(menu_item_id_raw)
    data = await state.get_data()
    unmatched = list(data.get("unmatched", []))
    if clause_index >= len(unmatched):
        await callback.answer("Open voice order again", show_alert=True)
        return
    clause = unmatched[clause_index]
    suggestion = next(
        (row for row in clause.get("suggestions", []) if row["menu_item_id"] == menu_item_id),
        None,
    )
    if not suggestion:
        await callback.answer("Suggestion expired", show_alert=True)
        return
    items = list(data.get("items", []))
    items.append(
        {
            "menu_item_id": menu_item_id,
            "name": suggestion["name"],
            "quantity": clause["quantity"],
            "modifier_ids": [],
            "modifier_names": [],
            "confidence": suggestion["confidence"],
            "source_text": clause["text"],
        }
    )
    unmatched.pop(clause_index)
    await state.update_data(items=items, unmatched=unmatched)
    await callback.answer()
    await _show_voice_review(callback.message, state)


@router.callback_query(VoiceOrder.reviewing, F.data == "voice_confirm")
async def confirm_voice_order(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    language = data.get("language", Language.RU.value)
    history = list(data.get("assistant_history", []))
    added: list[str] = []
    requested: list[str] = []
    errors: list[str] = []
    async with async_session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user:
            await callback.answer("Use /start first", show_alert=True)
            return
        for item in data.get("items", []):
            try:
                await add_to_cart(
                    session,
                    user.id,
                    data["event_id"],
                    item["menu_item_id"],
                    item["quantity"],
                    item.get("modifier_ids", []),
                )
                added.append(f"{item['quantity']} × {item['name']}")
            except DomainError as exc:
                errors.append(str(exc))
        for clause in data.get("unmatched", []):
            try:
                await create_special_request(
                    session,
                    user.id,
                    data["event_id"],
                    clause["text"],
                    clause["quantity"],
                    source_transcript=data.get("transcript", ""),
                )
                requested.append(f"{clause['quantity']} × {clause['text']}")
            except DomainError as exc:
                errors.append(str(exc))
    lines = []
    if added:
        lines.append("Added to cart:" if language == Language.EN.value else "Добавлено в корзину:")
        lines.extend(f"• {escape(line)}" for line in added)
    if requested:
        lines.append(
            "\nSent to the bartender for confirmation:"
            if language == Language.EN.value
            else "\nПередано бармену на подтверждение:"
        )
        lines.extend(f"• {escape(line)}" for line in requested)
    if errors:
        lines.append("\n" + escape(errors[0]))
    if not lines:
        lines.append(
            "Nothing was added." if language == Language.EN.value else "Ничего не добавлено."
        )
    lines.append(
        "\nWould you like anything else?"
        if language == Language.EN.value
        else "\nХотите заказать что-нибудь ещё?"
    )
    response_text = "\n".join(lines)
    history = _updated_assistant_history(
        history,
        "Confirm the current draft",
        response_text,
    )
    await state.set_state(Waiter.waiting)
    await state.set_data({"assistant_history": history})
    await callback.answer()
    await callback.message.edit_text(
        response_text,
        reply_markup=waiter_keyboard(language),
    )


@router.callback_query(VoiceOrder.reviewing, F.data == "voice_retry")
async def retry_voice_order(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    language = data.get("language", Language.RU.value)
    history = list(data.get("assistant_history", []))[-10:]
    await state.set_state(Waiter.waiting)
    await state.set_data({"assistant_history": history})
    await callback.answer()
    await callback.message.edit_text(
        (
            "Send the order again by text or voice."
            if language == Language.EN.value
            else "Отправьте заказ заново текстом или голосом."
        ),
        reply_markup=waiter_keyboard(language),
    )


@router.callback_query(VoiceOrder.reviewing, F.data == "voice_catalog")
async def voice_order_catalog(callback: CallbackQuery, state: FSMContext) -> None:
    await show_categories(callback, state)


@router.callback_query(F.data == "recommend")
async def recommend_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    try:
        context = await _voice_context(callback.from_user.id)
        data = await state.get_data()
        text = (
            "Recommend something from the menu based on our conversation. "
            "If you don't know my preferences yet, ask one short question. "
            "Only suggest options; keep my current draft unchanged."
            if context["language"] == Language.EN.value
            else "Посоветуй что-нибудь из меню с учётом нашего разговора. "
            "Если ещё не знаешь мои предпочтения, задай один короткий вопрос. "
            "Пока только предложи варианты, текущий черновик не меняй."
        )
        result = await _assistant_response(
            text,
            context,
            history=data.get("assistant_history", []),
            current_draft=_current_draft(data),
        )
        # A recommendation button never creates or replaces an order draft.
        result.items = []
        result.unmatched = []
        result.needs_confirmation = False
        await _present_assistant_result(
            callback.message,
            state,
            text,
            context,
            result,
            edit=True,
            keep_existing_draft_on_question=True,
        )
    except DomainError as exc:
        await callback.message.answer(escape(str(exc)))


@router.message(Recommendation.waiting, F.text)
async def recommend_result(message: Message, state: FSMContext) -> None:
    await waiter_message(message, state)


@router.message(F.text)
async def automatic_waiter(message: Message, state: FSMContext) -> None:
    # Registered last so /start, /language and pending draft edits keep precedence.
    await waiter_message(message, state)
