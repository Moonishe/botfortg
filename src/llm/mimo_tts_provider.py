"""MiMo (Xiaomi) TTS провайдер — синтез речи через MiMo Audio API.

Модели: mimo-v2.5-tts, mimo-v2.5-tts-voiceclone, mimo-v2.5-tts-voicedesign
Base URL: https://api.xiaomimimo.com/v1
API docs: https://platform.xiaomimimo.com/docs/en-US/usage-guide/speech-synthesis-v2.5
"""

import httpx
from openai import AsyncOpenAI, AuthenticationError, PermissionDeniedError


MIMO_TTS_BASE_URL = "https://api.xiaomimimo.com/v1"


class MiMoTTSProvider:
    """TTS провайдер для Xiaomi MiMo Audio API."""

    name = "mimo-tts"

    def __init__(self, api_key: str, *, model: str = "mimo-v2.5-tts") -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=MIMO_TTS_BASE_URL,
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
        """Доступные голоса MiMo TTS."""
        return ["default"]

    async def close(self) -> None:
        await self._client.close()
