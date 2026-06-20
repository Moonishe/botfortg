import httpx
from collections.abc import AsyncGenerator
from openai import AsyncOpenAI

from src.llm._openai_compat_mixin import OpenAICompatEmbedMixin, OpenAICompatToolMixin
from src.llm.base_provider import BaseLLMProvider
from src.core.security.ssrf_guard import validate_base_url as _validate_base_url
from src.llm.base import ChatMessage


MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
MISTRAL_CHAT_LIGHT = "mistral-small-latest"
MISTRAL_CHAT_HEAVY = "mistral-medium-latest"


class MistralProvider(OpenAICompatToolMixin, OpenAICompatEmbedMixin, BaseLLMProvider):
    name = "mistral"
    _LIGHT_MODEL = MISTRAL_CHAT_LIGHT
    _HEAVY_MODEL = MISTRAL_CHAT_HEAVY

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
            base_url=base_url or MISTRAL_BASE_URL,
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
        async with stream:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
