from collections.abc import AsyncGenerator

import httpx
from openai import AsyncOpenAI

from src.config import settings
from src.llm._openai_compat_mixin import OpenAICompatEmbedMixin
from src.llm.base_provider import BaseLLMProvider
from src.core.security.ssrf_guard import validate_base_url as _validate_base_url
from src.llm.base import ChatMessage

CLOUDFLARE_CHAT_LIGHT = "@cf/qwen/qwen3-30b-a3b-fp8"
CLOUDFLARE_CHAT_HEAVY = "@cf/moonshotai/kimi-k2.6"


class CloudflareProvider(OpenAICompatEmbedMixin, BaseLLMProvider):
    """Cloudflare Workers AI провайдер (OpenAI-совместимый API).

    Использует AsyncOpenAI с кастомным base_url на Cloudflare Accounts AI Gateway.
    Поддерживает chat (Kimi K2.6, Qwen3) и embeddings (BGE-M3).

    Наследует:
    - BaseLLMProvider — _resolve_model(), _fmt_messages(), name, _model, _embed_model
    - OpenAICompatEmbedMixin — validate_key(), list_models(), close(), embed(), embed_batch()
    """

    name = "cloudflare"
    _LIGHT_MODEL = CLOUDFLARE_CHAT_LIGHT
    _HEAVY_MODEL = CLOUDFLARE_CHAT_HEAVY

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
    ) -> None:
        url = _validate_base_url(base_url)
        if not url:
            account_id = settings.cloudflare_account_id
            if not account_id:
                raise ValueError(
                    "CLOUDFLARE_ACCOUNT_ID не задан в .env. "
                    "Добавь CLOUDFLARE_ACCOUNT_ID=<твой account_id> в .env"
                )
            url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
        # Базовая инициализация: сохраняет api_key, model, embed_model
        super().__init__(
            api_key=api_key,
            base_url=None,
            model=model,
            embed_model=embed_model,
        )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=url,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

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
