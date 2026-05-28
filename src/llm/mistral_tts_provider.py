"""Mistral TTS провайдер — синтез речи через Mistral Audio API.

Модели: mistral-tts, mistral-small-tts
Base URL: https://api.mistral.ai/v1
API docs: https://docs.mistral.ai/capabilities/audio/
"""

import httpx
from openai import AsyncOpenAI, AuthenticationError, PermissionDeniedError
from openai import APIConnectionError


MISTRAL_TTS_BASE_URL = "https://api.mistral.ai/v1"


class MistralTTSProvider:
    """TTS провайдер для Mistral Audio API (OpenAI-совместимый)."""

    name = "mistral-tts"

    def __init__(self, api_key: str, *, model: str = "mistral-tts") -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=MISTRAL_TTS_BASE_URL,
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
        self, text: str, *, voice: str = "default", speed: float = 1.0
    ) -> bytes:
        """Синтезирует речь из текста. Возвращает аудио байты."""
        resp = await self._client.audio.speech.create(
            model=self._model,
            voice=voice,
            input=text,
            speed=speed,
        )
        return resp.content

    async def list_voices(self) -> list[str]:
        """Доступные голоса Mistral TTS."""
        return ["default"]

    async def close(self) -> None:
        await self._client.close()
