"""Custom OpenAI-совместимый провайдер — endpoint + модель из БД.

Используется для кастомных провайдеров, добавленных пользователем через
онбординг или /settings. Конфигурация (endpoint, model, key) читается
из LlmKeySlot, переданного в конструктор.
"""

import httpx
from collections.abc import AsyncGenerator
from openai import AsyncOpenAI

from src.llm._openai_compat_mixin import OpenAICompatEmbedMixin, OpenAICompatToolMixin
from src.llm.base_provider import BaseLLMProvider
from src.core.security.ssrf_guard import validate_base_url as _validate_base_url
from src.llm.base import ChatMessage


class CustomProvider(OpenAICompatToolMixin, OpenAICompatEmbedMixin, BaseLLMProvider):
    """Провайдер для кастомного OpenAI-совместимого endpoint.

    Конфигурация передаётся через конструктор, а не через Settings.
    Позволяет пользователю добавить любой OpenAI-совместимый API.
    """

    name = "custom"
    _LIGHT_MODEL = "default"
    _HEAVY_MODEL = "default"

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "",
        base_url: str = "",
        model: str | None = None,
        embed_model: str | None = None,
        label: str = "Custom",
    ) -> None:
        url = base_url or endpoint
        if not url:
            raise ValueError("CustomProvider requires base_url or endpoint")
        endpoint_safe = _validate_base_url(url)
        self._label = label
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=endpoint_safe,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        super().__init__(api_key=api_key, model=model, embed_model=embed_model)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
    ) -> str:
        model = self._resolve_model(heavy)
        resp = await self._client.chat.completions.create(
            model=model,
            messages=self._fmt_messages(messages),
        )
        return resp.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
    ) -> AsyncGenerator[str]:
        model = self._resolve_model(heavy)
        fmt = self._fmt_messages(messages)
        stream = await self._client.chat.completions.create(
            model=model, messages=fmt, stream=True
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
