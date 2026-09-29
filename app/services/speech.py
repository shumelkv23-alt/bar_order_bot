from __future__ import annotations

import httpx

DEFAULT_API_BASE_URL = "https://api.openai.com/v1"
MAX_MULTIPART_AUDIO_SIZE = 25 * 1024 * 1024


class SpeechRecognitionUnavailable(RuntimeError):
    pass


class SpeechToTextService:
    def __init__(
        self,
        api_key: str | None,
        model: str,
        base_url: str = DEFAULT_API_BASE_URL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or DEFAULT_API_BASE_URL).strip().rstrip("/")
        self.transport = transport

    async def transcribe(
        self,
        audio: bytes,
        filename: str = "voice.ogg",
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> str:
        if not self.api_key:
            raise SpeechRecognitionUnavailable(
                "Распознавание речи не настроено. Используйте текстовый заказ или каталог."
            )
        if not audio:
            raise SpeechRecognitionUnavailable("Голосовое сообщение оказалось пустым")
        if len(audio) > MAX_MULTIPART_AUDIO_SIZE:
            raise SpeechRecognitionUnavailable("Голосовое сообщение слишком большое")

        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": (filename, audio, "audio/ogg")}
        data = {"model": self.model}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt[:1000]

        try:
            async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers=headers,
                    files=files,
                    data=data,
                )
        except (httpx.TimeoutException, httpx.RequestError):
            raise SpeechRecognitionUnavailable("Сервис распознавания временно недоступен") from None

        if response.is_error:
            raise SpeechRecognitionUnavailable("Сервис распознавания временно недоступен")

        try:
            payload = response.json()
        except ValueError:
            raise SpeechRecognitionUnavailable(
                "Сервис распознавания вернул некорректный ответ"
            ) from None
        if not isinstance(payload, dict):
            raise SpeechRecognitionUnavailable("Сервис распознавания вернул некорректный ответ")

        text = str(payload.get("text", "")).strip()
        if not text:
            raise SpeechRecognitionUnavailable("Не удалось распознать речь")
        return text
