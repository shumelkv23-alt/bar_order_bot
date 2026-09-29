from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.config import get_settings
from app.domain import Language
from app.models import Category, MenuItem

TEXTS = {
    Language.RU.value: {
        "order": "Меню",
        "voice": "Голосом",
        "text_order": "Текстом",
        "recommend": "✨ Помоги выбрать",
        "waiter": "🕯 Ваш официант",
        "cart": "Корзина",
        "my_order": "Мой заказ",
        "settings": "Язык",
        "back": "‹ Назад",
        "add": "＋ В корзину",
        "checkout": "✓ Отправить заказ",
        "cancel": "Отменить заказ",
        "edit_order": "✏️ Изменить заказ",
        "remove": "Удалить",
        "minus": "−",
        "plus": "+",
    },
    Language.EN.value: {
        "order": "Catalog",
        "voice": "By voice",
        "text_order": "By text",
        "recommend": "✨ Help me choose",
        "waiter": "🕯 Your waiter",
        "cart": "Cart",
        "my_order": "My order",
        "settings": "Language",
        "back": "‹ Back",
        "add": "＋ Add to cart",
        "checkout": "✓ Send order",
        "cancel": "Cancel order",
        "edit_order": "✏️ Edit order",
        "remove": "Remove",
        "minus": "−",
        "plus": "+",
    },
}

CATALOG_PAGE_SIZE = 6


def _rows_of_two(buttons: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    return [buttons[index : index + 2] for index in range(0, len(buttons), 2)]


def _category_icon(category: Category) -> str:
    normalized = category.name_en.casefold()
    if "cocktail" in normalized:
        return "🍸"
    if "beer" in normalized or "wine" in normalized:
        return "🍷"
    if "non-alcohol" in normalized:
        return "🥤"
    if "snack" in normalized:
        return "🥨"
    if "food" in normalized:
        return "🍽"
    return "•"


def _compact(text: str, limit: int = 18) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Русский", callback_data="lang:ru"),
                InlineKeyboardButton(text="English", callback_data="lang:en"),
            ]
        ]
    )


def main_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    return waiter_keyboard(language)


def waiter_keyboard(language: str) -> InlineKeyboardMarkup:
    mini_app_url = get_settings().mini_app_url
    menu_button = (
        InlineKeyboardButton(
            text="Открыть меню" if language == Language.RU.value else "Open menu",
            web_app=WebAppInfo(url=mini_app_url),
        )
        if mini_app_url and mini_app_url.startswith("https://")
        else InlineKeyboardButton(
            text="Открыть меню" if language == Language.RU.value else "Open menu",
            callback_data="menu",
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть заказ" if language == Language.RU.value else "Open order",
                    callback_data="open_order",
                ),
                menu_button,
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "Дайте мне рекомендацию"
                        if language == Language.RU.value
                        else "Recommend something"
                    ),
                    callback_data="recommend",
                )
            ],
        ]
    )


def categories_keyboard(categories: list[Category], language: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=(
                f"{_category_icon(category)} "
                f"{category.name_en if language == Language.EN.value else category.name_ru}"
            ),
            callback_data=f"cat:{category.id}:0",
        )
        for category in categories
    ]
    rows = _rows_of_two(buttons)
    rows.append(
        [
            InlineKeyboardButton(text=TEXTS[language]["back"], callback_data="main"),
            InlineKeyboardButton(text=f"🧺 {TEXTS[language]['cart']}", callback_data="cart"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def items_keyboard(
    items: list[MenuItem],
    language: str,
    *,
    category_id: int,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=item.name_en if language == Language.EN.value else item.name_ru,
            callback_data=f"item:{item.id}:{page}",
        )
        for item in items
    ]
    rows = _rows_of_two(buttons)
    if total_pages > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text="‹",
                    callback_data=f"cat:{category_id}:{page - 1}" if page > 0 else "noop",
                ),
                InlineKeyboardButton(text=f"{page + 1} / {total_pages}", callback_data="noop"),
                InlineKeyboardButton(
                    text="›",
                    callback_data=(
                        f"cat:{category_id}:{page + 1}" if page + 1 < total_pages else "noop"
                    ),
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=TEXTS[language]["back"], callback_data="menu"),
            InlineKeyboardButton(text=f"🧺 {TEXTS[language]['cart']}", callback_data="cart"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def item_draft_keyboard(
    item: MenuItem,
    selected: set[int],
    quantity: int,
    language: str,
    max_quantity: int = 3,
    category_page: int = 0,
) -> InlineKeyboardMarkup:
    modifier_buttons: list[InlineKeyboardButton] = []
    for link in item.allowed_modifiers:
        modifier = link.modifier
        name = modifier.name_en if language == Language.EN.value else modifier.name_ru
        prefix = "✓ " if modifier.id in selected else "○ "
        modifier_buttons.append(
            InlineKeyboardButton(text=prefix + name, callback_data=f"mod:{modifier.id}")
        )
    rows = _rows_of_two(modifier_buttons)
    rows.append(
        [
            InlineKeyboardButton(
                text=TEXTS[language]["minus"],
                callback_data="qty:minus" if quantity > 1 else "noop",
            ),
            InlineKeyboardButton(
                text=(f"Qty {quantity}" if language == Language.EN.value else f"{quantity} шт."),
                callback_data="noop",
            ),
            InlineKeyboardButton(
                text=TEXTS[language]["plus"],
                callback_data="qty:plus" if quantity < max_quantity else "noop",
            ),
        ]
    )
    rows.append([InlineKeyboardButton(text=TEXTS[language]["add"], callback_data="draft:add")])
    rows.append(
        [
            InlineKeyboardButton(
                text=TEXTS[language]["back"],
                callback_data=f"cat:{item.category_id}:{category_page}",
            ),
            InlineKeyboardButton(text=f"🧺 {TEXTS[language]['cart']}", callback_data="cart"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cart_keyboard(
    language: str,
    items: list[dict],
    max_quantity: int = 3,
    has_active_order: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        item_id = item["id"]
        quantity = item["quantity"]
        rows.append(
            [
                InlineKeyboardButton(
                    text=TEXTS[language]["minus"],
                    callback_data=(
                        f"cartqty:{item_id}:{quantity}:minus" if quantity > 1 else "noop"
                    ),
                ),
                InlineKeyboardButton(
                    text=f"{quantity}× {_compact(item['name'])}",
                    callback_data="noop",
                ),
                InlineKeyboardButton(
                    text=TEXTS[language]["plus"],
                    callback_data=(
                        f"cartqty:{item_id}:{quantity}:plus" if quantity < max_quantity else "noop"
                    ),
                ),
                InlineKeyboardButton(
                    text="×",
                    callback_data=f"cartdel:{item_id}:{quantity}",
                ),
            ]
        )
    if items:
        rows.append(
            [InlineKeyboardButton(text=TEXTS[language]["checkout"], callback_data="checkout")]
        )
    if has_active_order:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Статус отправленного заказа" if language == "ru" else "Sent order status",
                    callback_data="my_order",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="＋ " + TEXTS[language]["order"], callback_data="menu"),
            InlineKeyboardButton(text=TEXTS[language]["back"], callback_data="main"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def active_order_keyboard(
    language: str,
    order_id: int,
    order_version: int,
    can_change: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_change:
        rows.append(
            [
                InlineKeyboardButton(
                    text=TEXTS[language]["edit_order"],
                    callback_data=f"edit_order:{order_id}:{order_version}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=TEXTS[language]["cancel"],
                    callback_data=f"cancel_order:{order_id}:{order_version}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=TEXTS[language]["cart"], callback_data="cart"),
            InlineKeyboardButton(text=TEXTS[language]["back"], callback_data="main"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def voice_review_keyboard(
    language: str,
    items: list[dict],
    unmatched: list[dict],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for clause_index, clause in enumerate(unmatched):
        for suggestion in clause.get("suggestions", [])[:2]:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"Похоже на: {suggestion['name']}"
                            if language == Language.RU.value
                            else f"Maybe: {suggestion['name']}"
                        ),
                        callback_data=(
                            f"voice_suggest:{clause_index}:{suggestion['menu_item_id']}"
                        ),
                    )
                ]
            )
    if items or unmatched:
        label = (
            "Подтвердить и отправить запрос"
            if unmatched and language == Language.RU.value
            else "Confirm and send request"
            if unmatched
            else "Добавить в корзину"
            if language == Language.RU.value
            else "Add to cart"
        )
        rows.append([InlineKeyboardButton(text=f"✓ {label}", callback_data="voice_confirm")])
    rows.append(
        [
            InlineKeyboardButton(
                text=("Попробовать заново" if language == Language.RU.value else "Try again"),
                callback_data="voice_retry",
            ),
            InlineKeyboardButton(
                text="Каталог" if language == Language.RU.value else "Catalog",
                callback_data="voice_catalog",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
