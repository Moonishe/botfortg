"""Grok (xAI) провайдер — OpenAI-совместимый API от xAI.

Модели: grok-4.3 (latest), grok-4.20-0309-reasoning, grok-4.20-0309-non-reasoning.
Base URL: https://api.x.ai/v1
API docs: https://docs.x.ai/docs

⚠️ xAI Grok не поддерживает embeddings. Провайдер использует OpenAICompatBaseMixin
(только chat + validate + list_models). Embeddings берутся из других провайдеров через fallback-цепочку.
"""

import httpx
from collections.abc import AsyncGenerator
from openai import AsyncOpenAI

from src.llm._openai_compat_mixin import OpenAICompatBaseMixin
from src.llm.base_provider import BaseLLMProvider
from src.core.security.ssrf_guard import validate_base_url as _validate_base_url
from src.llm.base import ChatMessage


GROK_BASE_URL = "https://api.x.ai/v1"
GROK_CHAT_LIGHT = "grok-4.3"
GROK_CHAT_HEAVY = "grok-4.20-0309-reasoning"


class GrokProvider(OpenAICompatBaseMixin, BaseLLMProvider):
    """Провайдер для Grok (xAI) — OpenAI-совместимый API. Без embeddings."""

    name = "grok"
    _LIGHT_MODEL = GROK_CHAT_LIGHT
    _HEAVY_MODEL = GROK_CHAT_HEAVY

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
    ) -> None:
        base_url = _validate_base_url(base_url)
        kwargs: dict = dict(
            api_key=api_key,
            base_url=base_url or GROK_BASE_URL,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self._client = AsyncOpenAI(**kwargs)
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
    ) -> AsyncGenerator[str, None]:
        model = self._resolve_model(heavy)
        fmt = self._fmt_messages(messages)
        stream = await self._client.chat.completions.create(
            model=model, messages=fmt, stream=True
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
