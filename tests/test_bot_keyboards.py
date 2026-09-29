from types import SimpleNamespace

from app.bot import keyboards
from app.bot.keyboards import (
    CATALOG_PAGE_SIZE,
    categories_keyboard,
    items_keyboard,
    main_menu_keyboard,
)
from app.models import Category, MenuItem


def test_main_menu_is_compact_and_keeps_primary_actions(monkeypatch) -> None:
    monkeypatch.setattr(keyboards, "get_settings", lambda: SimpleNamespace(mini_app_url=None))
    keyboard = main_menu_keyboard("ru")

    assert len(keyboard.inline_keyboard) == 2
    callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row}
    assert callbacks == {"open_order", "menu", "recommend"}


def test_main_menu_opens_miniapp_when_https_is_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        keyboards,
        "get_settings",
        lambda: SimpleNamespace(mini_app_url="https://bar.example.org/miniapp"),
    )
    keyboard = main_menu_keyboard("en")
    menu_button = keyboard.inline_keyboard[0][1]
    assert menu_button.web_app.url == "https://bar.example.org/miniapp"
    assert menu_button.callback_data is None


def test_categories_use_two_columns_and_one_footer_row() -> None:
    categories = [
        Category(id=index, name_ru=f"Раздел {index}", name_en=f"Section {index}")
        for index in range(1, 6)
    ]

    keyboard = categories_keyboard(categories, "ru")

    assert [len(row) for row in keyboard.inline_keyboard] == [2, 2, 1, 2]
    assert keyboard.inline_keyboard[0][0].callback_data == "cat:1:0"
    assert keyboard.inline_keyboard[-1][1].callback_data == "cart"


def test_catalog_page_never_shows_more_than_three_item_rows() -> None:
    items = [
        MenuItem(id=index, category_id=10, name_ru=f"Позиция {index}", name_en=f"Item {index}")
        for index in range(1, CATALOG_PAGE_SIZE + 1)
    ]

    keyboard = items_keyboard(
        items,
        "ru",
        category_id=10,
        page=0,
        total_pages=2,
    )

    assert [len(row) for row in keyboard.inline_keyboard] == [2, 2, 2, 3, 2]
    assert keyboard.inline_keyboard[0][0].callback_data == "item:1:0"
    assert keyboard.inline_keyboard[3][2].callback_data == "cat:10:1"
    assert keyboard.inline_keyboard[-1][1].callback_data == "cart"
