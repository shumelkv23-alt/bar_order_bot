from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot import handlers, keyboards
from app.db import Base
from app.models import Order, SpecialRequest
from app.services.assistant import AssistantResponse
from app.services.orders import add_to_cart, get_active_event, list_event_menu, submit_cart
from app.services.seed import seed_demo_data


@pytest.fixture
async def flow(monkeypatch):
    monkeypatch.setattr(keyboards, "get_settings", lambda: SimpleNamespace(mini_app_url=None))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_demo_data(session)
    monkeypatch.setattr(handlers, "async_session_factory", factory)
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=101, user_id=101))
    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        from_user=SimpleNamespace(
            id=101,
            full_name="Guest",
            username=None,
            language_code="en",
        ),
        answer=AsyncMock(return_value=status),
        edit_text=AsyncMock(),
        text="Something sour",
    )
    callback = SimpleNamespace(
        from_user=message.from_user,
        message=message,
        answer=AsyncMock(),
        data="menu",
    )
    yield SimpleNamespace(
        state=state,
        message=message,
        callback=callback,
        factory=factory,
        status=status,
    )
    await storage.close()
    await engine.dispose()


def callbacks(markup):
    return {button.callback_data for row in markup.inline_keyboard for button in row}


async def set_confirm_callback(flow):
    data = await flow.state.get_data()
    flow.callback.data = f"voice_confirm:{data['request_key']}"


async def test_start_enters_waiter_immediately_and_keeps_saved_language(flow):
    await handlers.start(flow.message, flow.state)
    assert await flow.state.get_state() == handlers.Waiter.waiting.state
    sent = flow.message.answer.call_args
    assert "I'm your waiter" in sent.args[0]
    assert callbacks(sent.kwargs["reply_markup"]) == {"open_order", "menu", "recommend"}

    flow.callback.data = "lang:ru"
    await handlers.choose_language(flow.callback, flow.state)
    await handlers.start(flow.message, flow.state)
    assert "Я ваш официант" in flow.message.answer.call_args.args[0]


async def test_catalog_navigation_preserves_conversation_and_review(flow):
    await handlers.start(flow.message, flow.state)
    context = await handlers._voice_context(101)
    item = context["assistant_menu"][0]
    history = [{"role": "user", "content": "Something sour"}]
    await flow.state.update_data(assistant_history=history)
    result = AssistantResponse(
        intent="order",
        reply="Please confirm",
        items=[{"menu_item_id": item.id, "quantity": 1}],
    )
    await handlers._present_assistant_result(
        flow.status,
        flow.state,
        "That one",
        context,
        result,
        edit=True,
    )
    before = await flow.state.get_data()
    await handlers.show_categories(flow.callback, flow.state)
    assert (await flow.state.get_data())["assistant_history"] == before["assistant_history"]
    flow.callback.data = "open_order"
    await handlers.open_current_order(flow.callback, flow.state)
    assert any(
        value.startswith("voice_confirm:")
        for value in callbacks(flow.message.edit_text.call_args.kwargs["reply_markup"])
    )
    assert (await flow.state.get_data())["items"] == before["items"]


@pytest.mark.parametrize("raw_state", [None, handlers.ItemDraft.choosing.state])
async def test_text_without_waiter_mode_is_routed_to_waiter(flow, monkeypatch, raw_state):
    waiter = AsyncMock()
    monkeypatch.setattr(handlers, "waiter_message", waiter)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=101, type="private"),
        from_user=User(id=101, is_bot=False, first_name="Guest"),
        text="I'd like something fresh",
    )
    await handlers.router.propagate_event(
        "message",
        message,
        state=flow.state,
        raw_state=raw_state,
        bot=AsyncMock(),
    )
    waiter.assert_awaited_once_with(message, flow.state)


async def test_open_order_shows_cart_then_submitted_status(flow):
    await handlers.start(flow.message, flow.state)
    async with flow.factory() as session:
        user = await handlers._user(session, 101)
        event = await get_active_event(session)
        entries = await list_event_menu(session, event.id)
        await add_to_cart(session, user.id, event.id, entries[0].menu_item_id, 1)
    await handlers.open_current_order(flow.callback, flow.state)
    assert "checkout" in callbacks(flow.message.edit_text.call_args.kwargs["reply_markup"])
    async with flow.factory() as session:
        await submit_cart(session, user.id, event.id)
    await handlers.open_current_order(flow.callback, flow.state)
    assert "submitted" in flow.message.edit_text.call_args.args[0]

    async with flow.factory() as session:
        await add_to_cart(session, user.id, event.id, entries[0].menu_item_id, 1)
    await handlers.open_current_order(flow.callback, flow.state)
    controls = callbacks(flow.message.edit_text.call_args.kwargs["reply_markup"])
    assert {"checkout", "my_order"} <= controls


async def test_voice_after_browsing_keeps_history_and_requires_confirmation(flow, monkeypatch):
    await handlers.start(flow.message, flow.state)
    context = await handlers._voice_context(101)
    item = context["assistant_menu"][0]
    history = [{"role": "user", "content": "Something sour"}]
    await flow.state.update_data(assistant_history=history)
    await handlers.show_categories(flow.callback, flow.state)
    ai = AsyncMock(
        return_value=AssistantResponse(
            intent="order",
            reply="Please confirm",
            items=[{"menu_item_id": item.id, "quantity": 1}],
        )
    )
    monkeypatch.setattr(handlers, "_assistant_response", ai)
    monkeypatch.setattr(
        handlers.SpeechToTextService,
        "transcribe",
        AsyncMock(return_value="That one please"),
    )
    flow.message.voice = SimpleNamespace(file_id="voice")
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="voice.ogg")),
        download_file=AsyncMock(),
    )
    await handlers.receive_voice_order(flow.message, bot, flow.state)
    assert ai.call_args.kwargs["history"] == history
    assert await flow.state.get_state() == handlers.VoiceOrder.reviewing.state
    assert any(
        value.startswith("voice_confirm:")
        for value in callbacks(flow.status.edit_text.call_args.kwargs["reply_markup"])
    )
    refreshed = await handlers._voice_context(101)
    assert refreshed["current_cart"] == []


async def test_recommendation_button_uses_history_and_cannot_add_items(flow, monkeypatch):
    await handlers.start(flow.message, flow.state)
    context = await handlers._voice_context(101)
    item = context["assistant_menu"][0]
    history = [{"role": "user", "content": "Something sour"}]
    await flow.state.update_data(assistant_history=history)
    ai = AsyncMock(
        return_value=AssistantResponse(
            intent="recommend",
            reply="Try this one",
            recommendation_ids=[item.id],
            items=[{"menu_item_id": item.id, "quantity": 1}],
        )
    )
    monkeypatch.setattr(handlers, "_assistant_response", ai)
    await handlers.recommend_prompt(flow.callback, flow.state)
    assert ai.call_args.kwargs["history"] == history
    data = await flow.state.get_data()
    assert not data.get("items")
    assert item.name in data["assistant_history"][-1]["content"]
    assert callbacks(flow.message.edit_text.call_args.kwargs["reply_markup"]) == {
        "open_order",
        "menu",
        "recommend",
    }


async def test_waiter_confirm_sends_full_order_and_clears_cart(flow):
    await handlers.start(flow.message, flow.state)
    context = await handlers._voice_context(101)
    first, second = context["assistant_menu"][:2]
    async with flow.factory() as session:
        user = await handlers._user(session, 101)
        await add_to_cart(session, user.id, context["event_id"], first.id, 1)
    context = await handlers._voice_context(101)
    result = AssistantResponse(
        intent="order",
        reply="Please confirm",
        items=[{"menu_item_id": second.id, "quantity": 2}],
    )
    await handlers._present_assistant_result(
        flow.status, flow.state, "Two more", context, result, edit=True
    )
    review = flow.status.edit_text.call_args.args[0]
    assert first.name in review and second.name in review
    await set_confirm_callback(flow)
    await handlers.confirm_voice_order(flow.callback, flow.state)
    async with flow.factory() as session:
        orders = list((await session.scalars(select(Order))).all())
        cart = await handlers.get_cart(session, user.id, context["event_id"])
    assert len(orders) == 1
    assert {item.menu_item_id: item.quantity for item in orders[0].items} == {
        first.id: 1,
        second.id: 2,
    }
    assert not cart.items
    assert orders[0].public_number in flow.message.edit_text.call_args.args[0]
    assert await flow.state.get_state() == handlers.Waiter.waiting.state


async def test_send_order_words_require_confirmation_and_text_yes_sends(flow):
    await handlers.start(flow.message, flow.state)
    context = await handlers._voice_context(101)
    item = context["assistant_menu"][0]
    async with flow.factory() as session:
        user = await handlers._user(session, 101)
        await add_to_cart(session, user.id, context["event_id"], item.id, 1)
    flow.message.text = "Отправь заказ"
    await handlers.waiter_message(flow.message, flow.state)
    assert await flow.state.get_state() == handlers.VoiceOrder.reviewing.state
    assert item.name in flow.status.edit_text.call_args.args[0]
    async with flow.factory() as session:
        assert not (await session.scalars(select(Order))).all()
    flow.message.text = "Да"
    await handlers.revise_voice_order(flow.message, flow.state)
    async with flow.factory() as session:
        assert len((await session.scalars(select(Order))).all()) == 1


async def test_waiter_rechecks_changed_cart_before_sending(flow):
    await handlers.start(flow.message, flow.state)
    context = await handlers._voice_context(101)
    first, second = context["assistant_menu"][:2]
    async with flow.factory() as session:
        user = await handlers._user(session, 101)
        await add_to_cart(session, user.id, context["event_id"], first.id, 1)
    context = await handlers._voice_context(101)
    await handlers._begin_cart_review(flow.status, flow.state, "Send my order", context)
    async with flow.factory() as session:
        await add_to_cart(session, user.id, context["event_id"], second.id, 1)
    await set_confirm_callback(flow)
    await handlers.confirm_voice_order(flow.callback, flow.state)
    async with flow.factory() as session:
        assert not (await session.scalars(select(Order))).all()
    assert second.name in flow.message.edit_text.call_args.args[0]
    await set_confirm_callback(flow)
    await handlers.confirm_voice_order(flow.callback, flow.state)
    async with flow.factory() as session:
        assert len((await session.scalars(select(Order))).all()) == 1


async def test_waiter_invalid_item_rolls_back_special_request(flow):
    await handlers.start(flow.message, flow.state)
    context = await handlers._voice_context(101)
    await flow.state.set_state(handlers.VoiceOrder.reviewing)
    await flow.state.update_data(
        language=context["language"],
        event_id=context["event_id"],
        cart_snapshot=[],
        request_key="f067cffb-2315-4af5-ac1c-bba0fe65c838",
        transcript="Unknown drink and bad item",
        items=[{"menu_item_id": 99999, "quantity": 1, "name": "Bad"}],
        unmatched=[{"text": "Unknown drink", "quantity": 1}],
    )
    await set_confirm_callback(flow)
    await handlers.confirm_voice_order(flow.callback, flow.state)
    async with flow.factory() as session:
        assert not (await session.scalars(select(Order))).all()
        assert not (await session.scalars(select(SpecialRequest))).all()
    assert await flow.state.get_state() == handlers.VoiceOrder.reviewing.state


async def test_old_review_button_cannot_confirm_revised_order(flow):
    await handlers.start(flow.message, flow.state)
    context = await handlers._voice_context(101)
    first, second = context["assistant_menu"][:2]
    await handlers._present_assistant_result(
        flow.status,
        flow.state,
        "First",
        context,
        AssistantResponse(
            intent="order", reply="Confirm", items=[{"menu_item_id": first.id, "quantity": 1}]
        ),
        edit=True,
    )
    await set_confirm_callback(flow)
    old_callback = flow.callback.data
    await handlers._present_assistant_result(
        flow.status,
        flow.state,
        "Correction",
        context,
        AssistantResponse(
            intent="order", reply="Confirm", items=[{"menu_item_id": second.id, "quantity": 1}]
        ),
        edit=True,
    )
    await handlers.confirm_voice_order(flow.callback, flow.state)
    async with flow.factory() as session:
        assert not (await session.scalars(select(Order))).all()
    assert flow.callback.data == old_callback
    assert flow.callback.answer.call_args.kwargs["show_alert"] is True
    await set_confirm_callback(flow)
    await handlers.confirm_voice_order(flow.callback, flow.state)
    async with flow.factory() as session:
        order = (await session.scalars(select(Order))).one()
    assert [item.menu_item_id for item in order.items] == [second.id]


async def test_special_only_request_is_idempotent(flow):
    await handlers.start(flow.message, flow.state)
    context = await handlers._voice_context(101)
    await flow.state.set_state(handlers.VoiceOrder.reviewing)
    await flow.state.update_data(
        language=context["language"],
        event_id=context["event_id"],
        cart_snapshot=[],
        request_key="ac92d168-e015-4430-a4a8-74bd3597d98c",
        transcript="Something unknown",
        items=[],
        unmatched=[{"text": "Something unknown", "quantity": 1}],
    )
    original_data = await flow.state.get_data()
    await set_confirm_callback(flow)
    await handlers.confirm_voice_order(flow.callback, flow.state)
    await flow.state.set_state(handlers.VoiceOrder.reviewing)
    await flow.state.set_data(original_data)
    await handlers.confirm_voice_order(flow.callback, flow.state)
    async with flow.factory() as session:
        requests = list((await session.scalars(select(SpecialRequest))).all())
    assert len(requests) == 1
    assert requests[0].request_key == original_data["request_key"]
