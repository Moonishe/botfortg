"""MiMo (Xiaomi) провайдер — OpenAI-совместимый API.

Модели: mimo-v2.5-pro, mimo-v2.5, mimo-v2-flash, mimo-v2-omni.
Regional endpoints: EU, US, Asia (default).
API docs: https://platform.xiaomimimo.com/docs/en-US/welcome
"""

import httpx
from collections.abc import AsyncGenerator
from openai import AsyncOpenAI

from src.llm._openai_compat_mixin import OpenAICompatEmbedMixin
from src.llm.base_provider import BaseLLMProvider
from src.core.security.ssrf_guard import validate_base_url as _validate_base_url
from src.llm.base import ChatMessage


MIMO_REGIONS: dict[str, str] = {
    "eu": "https://eu.api.xiaomimimo.com/v1",
    "us": "https://us.api.xiaomimimo.com/v1",
    "asia": "https://api.xiaomimimo.com/v1",
}
MIMO_DEFAULT_REGION = "asia"
MIMO_BASE_URL = MIMO_REGIONS[MIMO_DEFAULT_REGION]
MIMO_CHAT_LIGHT = "mimo-v2-flash"
MIMO_CHAT_HEAVY = "mimo-v2.5-pro"


class MiMoProvider(OpenAICompatEmbedMixin, BaseLLMProvider):
    """Провайдер для MiMo (Xiaomi) — OpenAI-совместимый API."""

    name = "mimo"
    _LIGHT_MODEL = MIMO_CHAT_LIGHT
    _HEAVY_MODEL = MIMO_CHAT_HEAVY

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        region: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
    ) -> None:
        # Resolve base_url: explicit base_url > region > default
        if base_url is None and region is not None:
            base_url = MIMO_REGIONS.get(region.lower(), MIMO_BASE_URL)
        base_url = _validate_base_url(base_url)
        kwargs: dict = dict(
            api_key=api_key,
            base_url=base_url or MIMO_BASE_URL,
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
    ) -> AsyncGenerator[str]:
        model = self._resolve_model(heavy)
        fmt = self._fmt_messages(messages)
        stream = await self._client.chat.completions.create(
            model=model, messages=fmt, stream=True
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
