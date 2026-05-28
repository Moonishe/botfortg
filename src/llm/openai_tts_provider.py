"""OpenAI TTS провайдер — синтез речи через OpenAI Audio API.

Модели: tts-1, tts-1-hd
Base URL: https://api.openai.com/v1
API docs: https://platform.openai.com/docs/guides/text-to-speech
"""

import httpx
from openai import AsyncOpenAI, AuthenticationError, PermissionDeniedError


OPENAI_TTS_BASE_URL = "https://api.openai.com/v1"


class OpenAITTSProvider:
    """TTS провайдер для OpenAI Audio API."""

    name = "openai-tts"

    def __init__(self, api_key: str, *, model: str = "tts-1") -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=OPENAI_TTS_BASE_URL,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self._model = model

    async def validate_key(self) -> bool:
        """Проверяет ключ через models.list()."""
        try:
            await self._client.models.list()
            return True
        except (AuthenticationError, PermissionDeniedError):
            return False
        except Exception:
            return False

    async def synthesize(
        self, text: str, *, voice: str = "alloy", speed: float = 1.0
    ) -> bytes:
        """Синтезирует речь из текста. Возвращает MP3 байты."""
        resp = await self._client.audio.speech.create(
            model=self._model,
            voice=voice,
            input=text,
            speed=speed,
        )
        return resp.content

    async def list_voices(self) -> list[str]:
        """Доступные голоса OpenAI TTS."""
        return ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

    async def close(self) -> None:
        await self._client.close()
