from __future__ import annotations

import httpx
import pytest

from app.services.speech import SpeechRecognitionUnavailable, SpeechToTextService


@pytest.mark.asyncio
async def test_transcribe_uses_configured_openrouter_multipart_endpoint() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/v1/audio/transcriptions"
        assert request.headers["Authorization"] == "Bearer test-openrouter-key"
        assert request.headers["Content-Type"].startswith("multipart/form-data;")
        body = await request.aread()
        assert b"openai/whisper-large-v3-turbo" in body
        assert b'filename="voice.ogg"' in body
        assert b"telegram-audio" in body
        assert b'name="language"' in body
        assert b"ru" in body
        assert b'name="prompt"' in body
        assert b"Mojito" in body
        return httpx.Response(200, json={"text": "  two mojitos  "})

    service = SpeechToTextService(
        api_key="test-openrouter-key",
        model="openai/whisper-large-v3-turbo",
        base_url="https://openrouter.ai/api/v1/",
        transport=httpx.MockTransport(handler),
    )

    assert (
        await service.transcribe(
            b"telegram-audio",
            language="ru",
            prompt="Mojito, Gin and Tonic",
        )
        == "two mojitos"
    )


@pytest.mark.asyncio
async def test_transcribe_keeps_openai_as_default_endpoint() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/audio/transcriptions"
        return httpx.Response(200, json={"text": "mojito"})

    service = SpeechToTextService(
        api_key="test-openai-key",
        model="gpt-4o-mini-transcribe",
        transport=httpx.MockTransport(handler),
    )

    assert await service.transcribe(b"audio") == "mojito"


@pytest.mark.asyncio
async def test_transcribe_requires_api_key_without_sending_request() -> None:
    service = SpeechToTextService(api_key=None, model="unused")

    with pytest.raises(SpeechRecognitionUnavailable, match="не настроено"):
        await service.transcribe(b"audio")


@pytest.mark.asyncio
async def test_transcribe_hides_provider_error_and_api_key() -> None:
    secret = "secret-that-must-not-leak"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"invalid key: {secret}")

    service = SpeechToTextService(
        api_key=secret,
        model="openai/whisper-large-v3-turbo",
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SpeechRecognitionUnavailable) as raised:
        await service.transcribe(b"audio")

    assert str(raised.value) == "Сервис распознавания временно недоступен"
    assert secret not in str(raised.value)


@pytest.mark.asyncio
async def test_transcribe_handles_network_and_invalid_json_errors() -> None:
    async def network_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("cannot connect", request=request)

    unavailable = SpeechToTextService(
        api_key="test-key",
        model="model",
        transport=httpx.MockTransport(network_error),
    )
    with pytest.raises(SpeechRecognitionUnavailable, match="временно недоступен"):
        await unavailable.transcribe(b"audio")

    async def invalid_json(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    invalid = SpeechToTextService(
        api_key="test-key",
        model="model",
        transport=httpx.MockTransport(invalid_json),
    )
    with pytest.raises(SpeechRecognitionUnavailable, match="некорректный ответ"):
        await invalid.transcribe(b"audio")
