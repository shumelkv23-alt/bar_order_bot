import httpx

from app.config import Settings
from app.services.cocktail_recipes import CocktailRecipeService


def drink(name: str, alcoholic: str, identifier: str) -> dict:
    return {
        "idDrink": identifier,
        "strDrink": name,
        "strAlcoholic": alcoholic,
        "strIngredient1": "Lemon juice",
        "strMeasure1": "30 ml",
        "strIngredient2": "Sugar syrup",
        "strMeasure2": "10 ml",
        "strInstructions": "Shake and strain.",
    }


async def test_recipe_search_returns_attributed_structured_recipe():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={"drinks": [drink("Lemonade", "Non alcoholic", "12345")]},
        )

    service = CocktailRecipeService("test-key", transport=httpx.MockTransport(respond))
    recipe = await service.find("Хочу безалкогольный лимонад", "Lemonade")
    assert seen[0].url.host == "www.thecocktaildb.com"
    assert seen[0].url.params["s"] == "Lemonade"
    assert recipe.name == "Lemonade"
    assert recipe.ingredients == ["30 ml Lemon juice", "10 ml Sugar syrup"]
    assert recipe.source_url == "https://www.thecocktaildb.com/drink/12345"


async def test_non_alcoholic_request_never_selects_alcoholic_recipe():
    service = CocktailRecipeService(
        "test-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"drinks": [drink("Mojito", "Alcoholic", "1")]},
            )
        ),
    )
    assert await service.find("Без алкоголя, пожалуйста", "Mojito") is None
    assert (
        await service.find("Mojito", "Mojito", requirements="Я за рулём") is None
    )


async def test_recipe_search_failure_returns_no_proposal():
    service = CocktailRecipeService(
        "test-key",
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    assert await service.find("Margarita", "Margarita") is None


async def test_recipe_with_excluded_ingredient_is_not_proposed():
    service = CocktailRecipeService(
        "test-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"drinks": [drink("Lemonade", "Non alcoholic", "12345")]}
            )
        ),
    )
    assert await service.find("Лимонад без сахара", "Lemonade") is None
    assert await service.find("Аллергия на лимон", "Lemonade") is None
    assert await service.find("Lemonade", "Lemonade", requirements="Аллергия на миндаль") is None
    assert await service.find("Без орехов", "Lemonade") is None
    assert await service.find("avoid nuts", "Lemonade") is None


async def test_recipe_method_must_respect_no_ice_request():
    icy = drink("Lemonade", "Non alcoholic", "12345")
    icy["strInstructions"] = "Build with ice."
    service = CocktailRecipeService(
        "test-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"drinks": [icy]})
        ),
    )
    assert await service.find("Lemonade without ice", "Lemonade") is None


def test_recipe_provider_requires_explicit_key():
    production = Settings(environment="production", cocktail_db_api_key=None)
    development = Settings(environment="development", cocktail_db_api_key=None)
    assert production.effective_cocktail_db_api_key is None
    assert development.effective_cocktail_db_api_key is None
