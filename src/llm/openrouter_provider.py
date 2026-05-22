"""OpenRouter провайдер — бесплатный доступ к DeepSeek V4 Flash и другим моделям.

OpenRouter предоставляет единый OpenAI-совместимый endpoint для 300+ моделей.
Free tier: 20 RPM, 200 RPD (1000 с $10 lifetime депозитом), 33 бесплатные модели.
Подробнее: https://openrouter.ai/docs/api/reference/limits

DeepSeek V4 Flash (free): 1M контекст, reasoning, coding — топ бесплатная модель.
"""

import httpx
from openai import (
    APIConnectionError,
    AsyncOpenAI,
    AuthenticationError,
    PermissionDeniedError,
)

from src.llm.base import ChatMessage


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash:free"
HEAVY_MODEL = "deepseek/deepseek-v4-flash:free"


class OpenRouterProvider:
    """Провайдер для OpenRouter free models (DeepSeek V4 Flash и другие).

    OpenAI-совместимый API. Не поддерживает embeddings (free tier без эмбеддингов).
    """

    name = "openrouter"

    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            timeout=httpx.Timeout(60.0, connect=10.0),
            default_headers={
                "HTTP-Referer": "https://github.com/tashfeenahmed/freellmapi",
                "X-Title": "TelegramHelper",
            },
        )

    async def validate_key(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except AuthenticationError:
            return False  # invalid/revoked key
        except PermissionDeniedError:
            return False  # key lacks permission
        except APIConnectionError:
            raise  # network issue (timeout, connection refused) — let caller retry
        except Exception:
            return False  # unknown error — assume invalid

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        model = HEAVY_MODEL if heavy else DEFAULT_MODEL
        resp = await self._client.chat.completions.create(
            model=model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            extra_headers={
                "X-Title": "TelegramHelper",
            },
        )
        return resp.choices[0].message.content or ""

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError(
            "OpenRouter free tier не поддерживает embeddings. "
            "Используй OpenAI или другой провайдер для эмбеддингов."
        )

    async def close(self) -> None:
        await self._client.close()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "OpenRouter free tier не поддерживает embeddings. "
            "Используй OpenAI или другой провайдер для эмбеддингов."
        )
