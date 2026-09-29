import json

import httpx
import pytest

from app.services.assistant import (
    AssistantMenuItem,
    AssistantResponse,
    AssistantUnavailable,
    MenuAssistantService,
    fallback_assistant_response,
    validate_assistant_response,
)


def menu() -> list[AssistantMenuItem]:
    return [
        AssistantMenuItem(
            id=1,
            name="Мохито",
            name_ru="Мохито",
            name_en="Mojito",
            aliases=["mojito"],
            taste_profile={"tastes": ["fresh", "sour"], "base": "rum"},
            modifiers={10: "Без льда"},
            modifier_aliases={10: ["no ice", "без льда"]},
        ),
        AssistantMenuItem(
            id=2,
            name="Лимонад",
            name_ru="Лимонад",
            name_en="Lemonade",
            aliases=["lemonade"],
            taste_profile={
                "tastes": ["fresh", "sour"],
                "base": "non_alcoholic",
                "alcoholic": False,
            },
            is_alcoholic=False,
        ),
    ]


def test_validation_removes_hallucinated_ids_and_modifiers() -> None:
    raw = AssistantResponse.model_validate(
        {
            "intent": "order",
            "reply": "Проверьте заказ",
            "items": [
                {
                    "menu_item_id": 1,
                    "quantity": 12,
                    "modifier_ids": [10, 999],
                    "confidence": 0.9,
                },
                {"menu_item_id": 999, "quantity": 1},
            ],
            "recommendation_ids": [999, 2, 2, 1],
        }
    )

    clean = validate_assistant_response(raw, menu(), max_same_item=3)

    assert len(clean.items) == 1
    assert clean.items[0].quantity == 3
    assert clean.items[0].modifier_ids == [10]
    assert clean.recommendation_ids == [2, 1]
    assert clean.needs_confirmation is True


async def test_service_parses_openai_compatible_json_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "test/model"
        assert body["response_format"] == {"type": "json_object"}
        assert body["temperature"] == 0.35
        context = json.loads(body["messages"][-1]["content"])
        assert context["conversation_history"][-1]["content"] == "Что посоветуете?"
        assert context["current_cart"][0]["menu_item_id"] == 2
        assert context["current_draft"]["items"][0]["menu_item_id"] == 1
        assert len(context["available_menu"]) == 2
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "intent": "order",
                                    "reply": "Проверьте заказ",
                                    "items": [
                                        {
                                            "menu_item_id": 1,
                                            "quantity": 2,
                                            "modifier_ids": [10],
                                            "confidence": 0.96,
                                        }
                                    ],
                                    "needs_confirmation": True,
                                }
                            )
                        }
                    }
                ]
            },
        )

    service = MenuAssistantService(
        "secret",
        "test/model",
        "https://provider.test/v1",
        transport=httpx.MockTransport(handler),
    )
    result = await service.respond(
        "два мохито без льда",
        language="ru",
        menu=menu(),
        max_same_item=3,
        history=[{"role": "user", "content": "Что посоветуете?"}],
        current_cart=[{"menu_item_id": 2, "quantity": 1}],
        current_draft={
            "items": [{"menu_item_id": 1, "quantity": 2, "modifier_ids": []}],
            "unmatched": [],
        },
    )

    assert result.intent == "order"
    assert result.items[0].menu_item_id == 1
    assert result.items[0].modifier_ids == [10]


async def test_service_prompt_supports_contextual_corrections() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system_prompt = body["messages"][0]["content"]
        assert "COMPLETE revised draft" in system_prompt
        assert "exactly one short" in system_prompt
        assert "current_cart" in system_prompt
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "intent": "clarify",
                                    "reply": "К какому из двух напитков убрать лёд?",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    service = MenuAssistantService(
        "secret",
        "test/model",
        "https://provider.test/v1",
        transport=httpx.MockTransport(handler),
    )
    result = await service.respond(
        "а его тогда без льда",
        language="ru",
        menu=menu(),
        max_same_item=3,
        current_draft={
            "items": [
                {"menu_item_id": 1, "quantity": 1},
                {"menu_item_id": 2, "quantity": 1},
            ]
        },
    )

    assert result.intent == "clarify"
    assert result.items == []


async def test_service_fails_closed_on_invalid_response() -> None:
    service = MenuAssistantService(
        "secret",
        "test/model",
        "https://provider.test/v1",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
    )

    with pytest.raises(AssistantUnavailable):
        await service.respond(
            "что-нибудь кислое",
            language="ru",
            menu=menu(),
            max_same_item=3,
        )


def test_fallback_can_recommend_without_external_ai() -> None:
    result = fallback_assistant_response(
        "хочу безалкогольное освежающее",
        language="ru",
        menu=menu(),
        max_same_item=3,
    )

    assert result.intent == "recommend"
    assert result.recommendation_ids == [2]
