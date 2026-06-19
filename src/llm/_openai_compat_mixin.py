"""Shared mixins for OpenAI-compatible providers.

NOTE: Используется openai>=1.0.0 API (AsyncOpenAI). Устаревший API
(openai.Completion, openai.Embedding) не используется. Все вызовы —
через client.chat.completions.create() и client.embeddings.create().

Hierarchy:
    OpenAICompatBaseMixin  — validate_key, list_models, close
        └── OpenAICompatEmbedMixin  — embed, embed_batch (requires _embed_model)
    OpenAICompatToolMixin  — chat_with_tools (requires _client, _resolve_model, _fmt_messages)

OpenRouter uses only the base (no embeddings).
OpenAI, DeepSeek, Mistral, Cloudflare use the full embed mixin.

Requires subclasses to set:
    self._client  — AsyncOpenAI-compatible client
    self._embed_model — str, embedding model name (embed mixin only)
"""

from __future__ import annotations

import logging
from typing import Any

from openai import APIConnectionError, AuthenticationError, PermissionDeniedError

from src.core.actions.embedding_cache import aget, aset
from src.llm.base import ChatMessage
from src.llm.tool_calling.models import (
    ChatResponse,
    ToolCall,
    ToolDefinition,
    safe_parse_tool_args,
)

logger = logging.getLogger(__name__)


class OpenAICompatBaseMixin:
    """Common OpenAI-compatible methods: validate_key, list_models, close.

    Used by providers that don't support embeddings (e.g., OpenRouter).
    """

    _client: Any  # AsyncOpenAI

    async def validate_key(self) -> bool:
        """Validate key — models.list() first, fallback to minimal chat."""
        try:
            await self._client.models.list()
            return True
        except AuthenticationError:
            return False
        except PermissionDeniedError:
            return False
        except APIConnectionError:
            raise
        except Exception:
            # fall through to chat-based fallback
            logger.debug("Non-critical error", exc_info=True)

        # Fallback: try a minimal chat completion for endpoints without /models
        try:
            model = getattr(self, "_model", None) or "gpt-3.5-turbo"
            await self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        models = await self._client.models.list()
        return [m.id for m in models.data]

    async def close(self) -> None:
        await self._client.close()


class OpenAICompatEmbedMixin(OpenAICompatBaseMixin):
    """Embedding + common OpenAI-compatible methods shared across providers."""

    _embed_model: str

    async def embed(self, text: str) -> list[float]:
        cached = await aget(text, self._embed_model)
        if cached is not None:
            return cached
        resp = await self._client.embeddings.create(model=self._embed_model, input=text)
        result = resp.data[0].embedding
        await aset(text, result, self._embed_model)
        return result

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        results: list[list[float] | None] = [None] * len(texts)
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []
        for i, t in enumerate(texts):
            cached = await aget(t, self._embed_model)
            if cached is not None:
                results[i] = cached
            else:
                uncached_texts.append(t)
                uncached_indices.append(i)

        if uncached_texts:
            resp = await self._client.embeddings.create(
                model=self._embed_model, input=uncached_texts
            )
            api_results = [d.embedding for d in resp.data]
            for idx, emb in zip(uncached_indices, api_results, strict=True):
                await aset(texts[idx], emb, self._embed_model)
                results[idx] = emb

        return results  # type: ignore[return-value]


class OpenAICompatToolMixin:
    """OpenAI-compatible `chat_with_tools` implementation shared across providers.

    Requires subclasses to set:
        self._client  — AsyncOpenAI-compatible client
    And inherit from BaseLLMProvider for:
        self._resolve_model(), self._fmt_messages(), self._tools_to_openai()
    """

    _client: Any  # AsyncOpenAI

    async def chat_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        task_type: str = "default",
    ) -> ChatResponse:
        heavy = task_type not in ("draft", "default", "memory", "classify")
        model = self._resolve_model(heavy)  # type: ignore[attr-defined]
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=self._fmt_messages(messages),  # type: ignore[attr-defined]
        )
        if tools:
            kwargs["tools"] = self._tools_to_openai(tools)  # type: ignore[attr-defined]
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        text = choice.message.content or ""
        tool_calls: list[ToolCall] | None = None
        if choice.message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=safe_parse_tool_args(tc.function.arguments),
                )
                for tc in choice.message.tool_calls
            ]
        return ChatResponse(text=text, tool_calls=tool_calls)
