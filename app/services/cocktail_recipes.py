"""Look up an attributed recipe for a drink missing from the event menu."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class RecipeProposal:
    name: str
    ingredients: list[str]
    instructions: str
    source_url: str


def _search_term(request: str, suggested: str) -> str:
    if suggested:
        term = suggested
    else:
        lowered = request.casefold()
        aliases = {
            "non alcoholic": "Lemonade",
            "no alcohol": "Lemonade",
            "alcohol free": "Lemonade",
            "mocktail": "Lemonade",
            "безалкогол": "Lemonade",
            "без алкоголя": "Lemonade",
            "пина колада": "Pina Colada",
            "маргарит": "Margarita",
            "дайкири": "Daiquiri",
            "негрони": "Negroni",
            "мохито": "Mojito",
            "виски сауэр": "Whiskey Sour",
            "лимонад": "Lemonade",
            "lemonade": "Lemonade",
            "coconut": "Pina Colada",
            "кокос": "Pina Colada",
            "mint": "Mojito",
            "мят": "Mojito",
            "fruity": "Fruit Punch",
            "фрукт": "Fruit Punch",
            "spicy": "Bloody Mary",
            "остр": "Bloody Mary",
            "sour": "Whiskey Sour",
            "кисл": "Sour",
        }
        term = next((name for fragment, name in aliases.items() if fragment in lowered), request)
    return re.sub(r"[^a-zA-Z0-9 -]", "", term)[:80].strip()


def _requires_non_alcoholic(request: str) -> bool:
    return bool(
        re.search(
            r"безалкогол|без алкоголя|не пью алкоголь|"
            r"non.?alcohol|alcohol.?free|no alcohol|mocktail|virgin|"
            r"за рул[её]м|я вожу|designated driver|driving tonight",
            request,
            re.IGNORECASE,
        )
    )


def _excluded_ingredients(request: str) -> set[str]:
    lowered = request.casefold()
    exclusions = {
        "без сахара": "sugar",
        "без молока": "milk",
        "без сливок": "cream",
        "без клубники": "strawberry",
        "без лимона": "lemon",
        "без лайма": "lime",
        "без льда": "ice",
        "without sugar": "sugar",
        "without milk": "milk",
        "without cream": "cream",
        "without strawberry": "strawberry",
        "without lemon": "lemon",
        "without lime": "lime",
        "without ice": "ice",
        "no ice": "ice",
        "avoid ice": "ice",
    }
    return {ingredient for phrase, ingredient in exclusions.items() if phrase in lowered}


def _has_unresolved_exclusion(request: str) -> bool:
    lowered = request.casefold()
    excluded_words = re.findall(
        r"\bбез\s+([а-яёa-z-]+)|\b(?:without|avoid|no)\s+([a-z-]+)", lowered
    )
    known = {
        "алкоголя", "alcohol", "льда", "ice", "sugar", "сахара", "milk", "молока",
        "cream", "сливок", "strawberry", "клубники", "lemon", "лимона",
        "lime", "лайма",
    }
    return any((ru or en) not in known for ru, en in excluded_words)


class CocktailRecipeService:
    def __init__(
        self,
        api_key: str | None,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def find(
        self, request: str, suggested_query: str = "", *, requirements: str = ""
    ) -> RecipeProposal | None:
        full_request = f"{requirements} {request}"
        if re.search(r"аллерг|allerg|непереносим|intoleran", full_request, re.IGNORECASE):
            return None
        if _has_unresolved_exclusion(full_request):
            return None
        query = _search_term(request, suggested_query)
        if not self.api_key or not query:
            return None
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    f"https://www.thecocktaildb.com/api/json/v1/{self.api_key}/search.php",
                    params={"s": query},
                )
                response.raise_for_status()
                drinks = response.json().get("drinks") or []
        except (httpx.HTTPError, ValueError, AttributeError, TypeError):
            return None
        if not isinstance(drinks, list):
            return None
        non_alcoholic = _requires_non_alcoholic(full_request)
        excluded = _excluded_ingredients(full_request)
        matches = [
            drink
            for drink in drinks
            if isinstance(drink, dict)
            and (not non_alcoholic or drink.get("strAlcoholic") == "Non alcoholic")
        ]
        matches.sort(
            key=lambda drink: (
                str(drink.get("strDrink", "")).casefold() != query.casefold(),
                str(drink.get("strDrink", "")).casefold(),
            )
        )
        for drink in matches:
            recipe = self._parse_drink(drink)
            recipe_text = " ".join([*recipe.ingredients, recipe.instructions]) if recipe else ""
            if recipe and not any(
                re.search(rf"\b{re.escape(term)}\b", recipe_text, re.IGNORECASE)
                for term in excluded
            ):
                return recipe
        return None

    @staticmethod
    def _parse_drink(drink: dict) -> RecipeProposal | None:
        drink_id = str(drink.get("idDrink") or "")
        name = str(drink.get("strDrink") or "").strip()[:100]
        instructions = str(drink.get("strInstructions") or "").strip()[:1200]
        if not drink_id.isdigit() or not name or not instructions:
            return None
        ingredients = []
        for index in range(1, 16):
            ingredient = str(drink.get(f"strIngredient{index}") or "").strip()
            if not ingredient:
                continue
            measure = str(drink.get(f"strMeasure{index}") or "").strip()
            ingredients.append(f"{measure} {ingredient}".strip()[:120])
        if not ingredients:
            return None
        return RecipeProposal(
            name=name,
            ingredients=ingredients,
            instructions=instructions,
            source_url=f"https://www.thecocktaildb.com/drink/{drink_id}",
        )
