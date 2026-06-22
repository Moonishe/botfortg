import httpx
from collections.abc import AsyncGenerator
from openai import AsyncOpenAI

from src.llm._openai_compat_mixin import OpenAICompatEmbedMixin, OpenAICompatToolMixin
from src.llm.base_provider import BaseLLMProvider
from src.core.security.ssrf_guard import validate_base_url as _validate_base_url
from src.llm.base import ChatMessage

OPENAI_CHAT_LIGHT = "gpt-5-mini"
OPENAI_CHAT_HEAVY = "gpt-5.5"


class OpenAIProvider(OpenAICompatToolMixin, OpenAICompatEmbedMixin, BaseLLMProvider):
    name = "openai"
    _LIGHT_MODEL = OPENAI_CHAT_LIGHT
    _HEAVY_MODEL = OPENAI_CHAT_HEAVY

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
    ) -> None:
        base_url = _validate_base_url(base_url)
        kwargs: dict = dict(api_key=api_key, timeout=httpx.Timeout(60.0, connect=10.0))
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
        super().__init__(api_key=api_key, model=model, embed_model=embed_model)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
        max_tokens: int | None = None,
    ) -> str:
        model = self._resolve_model(heavy)
        kwargs: dict = {"model": model, "messages": self._fmt_messages(messages)}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        resp = await self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str]:
        model = self._resolve_model(heavy)
        kwargs: dict = {
            "model": model,
            "messages": self._fmt_messages(messages),
            "stream": True,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        stream = await self._client.chat.completions.create(**kwargs)
        async with stream:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
