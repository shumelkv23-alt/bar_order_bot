from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import EventStatus
from app.models import Category, Event, EventMenuItem, MenuItem, MenuItemModifier, Modifier

CATEGORIES = [
    ("Cocktails", "Коктейли", "Cocktails", 10),
    ("Beer", "Пиво и вино", "Beer", 20),
    ("Non-alcoholic", "Безалкогольные", "Non-alcoholic", 30),
    ("Snacks", "Закуски", "Snacks", 40),
    ("Food", "Кухня", "Food", 50),
]

MODIFIERS = [
    ("no_ice", "Без льда", "No ice", "ice", ["без льда", "no ice"]),
    (
        "extra_ice",
        "Дополнительный лёд",
        "Extra ice",
        "ice",
        ["больше льда", "много льда", "extra ice"],
    ),
    (
        "extra_lime",
        "Дополнительный лайм",
        "Extra lime",
        "extra",
        ["больше лайма", "extra lime"],
    ),
    (
        "non_alcoholic",
        "Безалкогольная версия",
        "Alcohol-free version",
        "variant",
        ["безалкогольный", "без алкоголя", "alcohol free", "non alcoholic"],
    ),
    (
        "spicy_sauce",
        "Острый соус",
        "Spicy sauce",
        "extra",
        ["острый соус", "поострее", "spicy sauce"],
    ),
    ("no_sauce", "Без соуса", "No sauce", "extra", ["без соуса", "no sauce"]),
]


def _drink(
    category: str,
    name_ru: str,
    name_en: str,
    ingredients_ru: str,
    ingredients_en: str,
    aliases: list[str],
    tastes: list[str],
    base: str,
    popularity: int,
    *,
    alcoholic: bool = True,
    strength: str = "medium",
    moods: list[str] | None = None,
    modifiers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "name_ru": name_ru,
        "name_en": name_en,
        "description_ru": f"Популярный напиток на основе: {ingredients_ru}.",
        "description_en": f"A popular drink made with {ingredients_en}.",
        "ingredients_ru": ingredients_ru,
        "ingredients_en": ingredients_en,
        "aliases": aliases,
        "is_alcoholic": alcoholic,
        "taste_profile": {
            "tastes": tastes,
            "base": base,
            "alcoholic": alcoholic,
            "strength": strength,
            "moods": moods or [],
            "popularity": popularity,
        },
        "modifiers": modifiers if modifiers is not None else ["no_ice", "extra_ice"],
    }


def _food(
    category: str,
    name_ru: str,
    name_en: str,
    ingredients_ru: str,
    ingredients_en: str,
    aliases: list[str],
    popularity: int,
    tags: list[str],
    modifiers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "name_ru": name_ru,
        "name_en": name_en,
        "description_ru": f"Барная кухня: {ingredients_ru}.",
        "description_en": f"Bar food: {ingredients_en}.",
        "ingredients_ru": ingredients_ru,
        "ingredients_en": ingredients_en,
        "aliases": aliases,
        "is_alcoholic": False,
        "taste_profile": {
            "item_type": "food",
            "tastes": tags,
            "popularity": popularity,
        },
        "modifiers": modifiers if modifiers is not None else ["spicy_sauce", "no_sauce"],
    }


MENU_ITEMS = [
    _drink(
        "Cocktails",
        "Мохито",
        "Mojito",
        "ром, лайм, мята, содовая",
        "rum, lime, mint, soda",
        ["мохито", "mojito"],
        ["fresh", "sour", "sweet"],
        "rum",
        10,
        moods=["party", "summer"],
        modifiers=["no_ice", "extra_ice", "extra_lime", "non_alcoholic"],
    ),
    _drink(
        "Cocktails",
        "Джин-тоник",
        "Gin and Tonic",
        "джин, тоник, лайм",
        "gin, tonic, lime",
        ["джин тоник", "gin tonic", "gin and tonic"],
        ["fresh", "bitter"],
        "gin",
        9,
        moods=["relaxed", "classic"],
        modifiers=["no_ice", "extra_ice", "extra_lime"],
    ),
    _drink(
        "Cocktails",
        "Апероль Шприц",
        "Aperol Spritz",
        "апероль, игристое, содовая",
        "Aperol, sparkling wine, soda",
        ["апероль", "апероль спритц", "aperol spritz"],
        ["bitter", "fresh", "sweet"],
        "wine",
        9,
        strength="light",
        moods=["summer", "social"],
    ),
    _drink(
        "Cocktails",
        "Куба Либре",
        "Cuba Libre",
        "ром, кола, лайм",
        "rum, cola, lime",
        ["ром кола", "куба либре", "cuba libre"],
        ["sweet", "fresh"],
        "rum",
        8,
        moods=["party"],
        modifiers=["no_ice", "extra_ice", "extra_lime"],
    ),
    _drink(
        "Cocktails",
        "Виски Сауэр",
        "Whiskey Sour",
        "виски, лимон, сахарный сироп",
        "whiskey, lemon, sugar syrup",
        ["виски сауэр", "whiskey sour"],
        ["sour", "sweet"],
        "whiskey",
        8,
        strength="strong",
        moods=["classic", "evening"],
    ),
    _drink(
        "Cocktails",
        "Маргарита",
        "Margarita",
        "текила, апельсиновый ликёр, лайм",
        "tequila, orange liqueur, lime",
        ["маргарита", "margarita"],
        ["sour", "fresh"],
        "tequila",
        9,
        strength="strong",
        moods=["party", "summer"],
        modifiers=["no_ice", "extra_ice", "extra_lime"],
    ),
    _drink(
        "Cocktails",
        "Негрони",
        "Negroni",
        "джин, кампари, красный вермут",
        "gin, Campari, sweet vermouth",
        ["негрони", "negroni"],
        ["bitter", "herbal"],
        "gin",
        8,
        strength="strong",
        moods=["classic", "evening"],
    ),
    _drink(
        "Cocktails",
        "Лонг-Айленд",
        "Long Island Iced Tea",
        "водка, джин, ром, текила, кола",
        "vodka, gin, rum, tequila, cola",
        ["лонг айленд", "лонг", "long island"],
        ["sweet", "fresh"],
        "mixed",
        8,
        strength="strong",
        moods=["party"],
    ),
    _drink(
        "Cocktails",
        "Пина Колада",
        "Pina Colada",
        "ром, кокос, ананас",
        "rum, coconut, pineapple",
        ["пина колада", "pina colada"],
        ["sweet", "tropical"],
        "rum",
        8,
        moods=["vacation", "summer"],
        modifiers=["no_ice", "extra_ice", "non_alcoholic"],
    ),
    _drink(
        "Cocktails",
        "Кровавая Мэри",
        "Bloody Mary",
        "водка, томатный сок, специи",
        "vodka, tomato juice, spices",
        ["кровавая мэри", "bloody mary"],
        ["spicy", "savory"],
        "vodka",
        7,
        moods=["brunch", "bold"],
        modifiers=["no_ice", "extra_ice", "spicy_sauce"],
    ),
    _drink(
        "Beer",
        "Светлое пиво",
        "Light Beer",
        "светлый лагер",
        "light lager",
        ["светлое", "светлое пиво", "лагер", "light beer", "lager"],
        ["fresh", "bitter"],
        "beer",
        9,
        strength="light",
        moods=["social", "sports"],
        modifiers=[],
    ),
    _drink(
        "Beer",
        "Тёмное пиво",
        "Dark Beer",
        "тёмный эль",
        "dark ale",
        ["тёмное", "темное пиво", "dark beer", "dark ale"],
        ["bitter", "roasted"],
        "beer",
        7,
        strength="light",
        moods=["evening"],
        modifiers=[],
    ),
    _drink(
        "Beer",
        "Яблочный сидр",
        "Apple Cider",
        "яблочный сидр",
        "apple cider",
        ["сидр", "яблочный сидр", "cider"],
        ["sweet", "sour", "fresh"],
        "cider",
        7,
        strength="light",
        moods=["summer", "social"],
        modifiers=[],
    ),
    _drink(
        "Beer",
        "Белое вино",
        "White Wine",
        "сухое белое вино",
        "dry white wine",
        ["белое", "белое вино", "white wine"],
        ["dry", "fresh", "sour"],
        "wine",
        7,
        strength="light",
        moods=["dinner", "relaxed"],
        modifiers=[],
    ),
    _drink(
        "Beer",
        "Красное вино",
        "Red Wine",
        "сухое красное вино",
        "dry red wine",
        ["красное", "красное вино", "red wine"],
        ["dry", "rich"],
        "wine",
        7,
        strength="light",
        moods=["dinner", "evening"],
        modifiers=[],
    ),
    _drink(
        "Beer",
        "Игристое вино",
        "Sparkling Wine",
        "сухое игристое вино",
        "dry sparkling wine",
        ["игристое", "игристое вино", "sparkling wine", "prosecco"],
        ["dry", "fresh"],
        "wine",
        8,
        strength="light",
        moods=["celebration", "social"],
        modifiers=[],
    ),
    _drink(
        "Non-alcoholic",
        "Домашний лимонад",
        "Homemade Lemonade",
        "лимон, сироп, содовая",
        "lemon, syrup, soda",
        ["лимонад", "домашний лимонад", "lemonade"],
        ["fresh", "sour", "sweet"],
        "non_alcoholic",
        9,
        alcoholic=False,
        strength="none",
        moods=["summer"],
        modifiers=["no_ice", "extra_ice", "extra_lime"],
    ),
    _drink(
        "Non-alcoholic",
        "Кола",
        "Cola",
        "кола",
        "cola",
        ["кола", "cola", "coke"],
        ["sweet"],
        "non_alcoholic",
        8,
        alcoholic=False,
        strength="none",
        moods=["casual"],
        modifiers=["no_ice", "extra_ice"],
    ),
    _drink(
        "Non-alcoholic",
        "Тоник",
        "Tonic Water",
        "тоник",
        "tonic water",
        ["тоник", "tonic", "tonic water"],
        ["bitter", "fresh"],
        "non_alcoholic",
        6,
        alcoholic=False,
        strength="none",
        moods=["fresh"],
        modifiers=["no_ice", "extra_ice", "extra_lime"],
    ),
    _drink(
        "Non-alcoholic",
        "Газированная вода",
        "Sparkling Water",
        "газированная вода",
        "sparkling water",
        ["вода с газом", "газированная вода", "sparkling water"],
        ["fresh"],
        "non_alcoholic",
        6,
        alcoholic=False,
        strength="none",
        moods=["neutral"],
        modifiers=["no_ice", "extra_ice", "extra_lime"],
    ),
    _food(
        "Snacks",
        "Картофель фри",
        "French Fries",
        "картофель, соль",
        "potatoes, salt",
        ["фри", "картошка фри", "fries"],
        10,
        ["salty", "crispy"],
    ),
    _food(
        "Snacks",
        "Сырные палочки",
        "Cheese Sticks",
        "сыр, панировка",
        "cheese, breading",
        ["сырные палочки", "cheese sticks"],
        8,
        ["cheesy", "crispy"],
    ),
    _food(
        "Snacks",
        "Куриные крылья",
        "Chicken Wings",
        "куриные крылья, специи",
        "chicken wings, spices",
        ["крылышки", "куриные крылья", "wings"],
        9,
        ["spicy", "savory"],
    ),
    _food(
        "Snacks",
        "Начос",
        "Nachos",
        "кукурузные чипсы, сырный соус, сальса",
        "corn chips, cheese sauce, salsa",
        ["начос", "nachos"],
        8,
        ["salty", "spicy"],
    ),
    _food(
        "Snacks",
        "Луковые кольца",
        "Onion Rings",
        "лук, панировка",
        "onion, breading",
        ["луковые кольца", "onion rings"],
        7,
        ["salty", "crispy"],
    ),
    _food(
        "Food",
        "Бургер",
        "Burger",
        "говядина, булочка, сыр, овощи",
        "beef, bun, cheese, vegetables",
        ["бургер", "burger", "гамбургер"],
        10,
        ["savory", "filling"],
    ),
    _food(
        "Food",
        "Клаб-сэндвич",
        "Club Sandwich",
        "курица, бекон, овощи, тост",
        "chicken, bacon, vegetables, toast",
        ["клаб сэндвич", "сэндвич", "club sandwich"],
        8,
        ["savory", "filling"],
    ),
    _food(
        "Food",
        "Пицца Маргарита",
        "Margherita Pizza",
        "тесто, томаты, моцарелла",
        "dough, tomatoes, mozzarella",
        ["маргарита пицца", "пицца маргарита", "margherita pizza"],
        9,
        ["cheesy", "filling"],
        [],
    ),
    _food(
        "Food",
        "Салат Цезарь",
        "Caesar Salad",
        "салат, курица, сыр, соус",
        "lettuce, chicken, cheese, dressing",
        ["цезарь", "салат цезарь", "caesar salad"],
        8,
        ["fresh", "savory"],
        ["no_sauce"],
    ),
    _food(
        "Food",
        "Мясная тарелка",
        "Meat Platter",
        "мясные закуски, горчица, соленья",
        "cold cuts, mustard, pickles",
        ["мясная тарелка", "мясное ассорти", "meat platter"],
        7,
        ["savory", "sharing"],
        [],
    ),
]


async def seed_demo_data(session: AsyncSession) -> None:
    categories = {row.name_en: row for row in (await session.scalars(select(Category))).all()}
    for key, name_ru, name_en, sort_order in CATEGORIES:
        if key not in categories:
            row = Category(
                name_ru=name_ru,
                name_en=name_en,
                sort_order=sort_order,
            )
            session.add(row)
            categories[key] = row

    existing_modifiers = {
        row.name_en: row for row in (await session.scalars(select(Modifier))).all()
    }
    modifiers: dict[str, Modifier] = {}
    for key, name_ru, name_en, kind, aliases in MODIFIERS:
        row = existing_modifiers.get(name_en)
        if not row:
            row = Modifier(
                name_ru=name_ru,
                name_en=name_en,
                kind=kind,
                aliases=aliases,
            )
            session.add(row)
            existing_modifiers[name_en] = row
        modifiers[key] = row
    await session.flush()

    existing_items = {row.name_en: row for row in (await session.scalars(select(MenuItem))).all()}
    menu_items: list[MenuItem] = []
    item_specs: dict[int, dict[str, Any]] = {}
    for index, spec in enumerate(MENU_ITEMS, start=1):
        item = existing_items.get(spec["name_en"])
        if not item:
            item = MenuItem(
                category=categories[spec["category"]],
                name_ru=spec["name_ru"],
                name_en=spec["name_en"],
                description_ru=spec["description_ru"],
                description_en=spec["description_en"],
                ingredients_ru=spec["ingredients_ru"],
                ingredients_en=spec["ingredients_en"],
                aliases=spec["aliases"],
                is_alcoholic=spec["is_alcoholic"],
                taste_profile=spec["taste_profile"],
                sort_order=index * 10,
            )
            session.add(item)
            existing_items[spec["name_en"]] = item
        menu_items.append(item)
        item_specs[id(item)] = spec
    await session.flush()

    existing_links = set(
        (
            await session.execute(
                select(MenuItemModifier.menu_item_id, MenuItemModifier.modifier_id)
            )
        ).all()
    )
    for item in menu_items:
        for modifier_key in item_specs[id(item)]["modifiers"]:
            modifier = modifiers[modifier_key]
            if (item.id, modifier.id) not in existing_links:
                session.add(MenuItemModifier(menu_item_id=item.id, modifier_id=modifier.id))
                existing_links.add((item.id, modifier.id))

    event = await session.scalar(select(Event).where(Event.code == "demo"))
    if not event:
        now = datetime.now(UTC)
        event = Event(
            code="demo",
            name="Demo Bar Event",
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=12),
            status=EventStatus.ACTIVE.value,
            orders_enabled=True,
            max_items_per_order=5,
            max_same_item=3,
        )
        session.add(event)
        await session.flush()

    event_menu_ids = set(
        (
            await session.scalars(
                select(EventMenuItem.menu_item_id).where(EventMenuItem.event_id == event.id)
            )
        ).all()
    )
    for item in menu_items:
        if item.id not in event_menu_ids:
            session.add(EventMenuItem(event_id=event.id, menu_item_id=item.id, is_available=True))
    await session.commit()
