"""ProviderFallback — цепочка fallback между MultiKeyProvider."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.core.infra.key_guard import safe_str
from src.core.infra.telemetry import start_span
from src.llm.base import ChatMessage, TaskType
from src.llm.tool_calling.models import ChatResponse, ToolDefinition

if TYPE_CHECKING:
    from src.llm.router import MultiKeyProvider

# Sentinel re-exported from router to keep the same semantic.
from src.llm.router import (
    ExhaustedError,
    _UNSET,
    _is_retryable_llm_error,
)
from src.llm.provider_manager import _score_provider

logger = logging.getLogger(__name__)


@dataclass
class ProviderFallback:
    """Primary provider with chat fallback to other configured providers.

    Embeddings intentionally stay on the primary provider to avoid mixing vector
    dimensions in Qdrant.
    """

    providers: list[MultiKeyProvider]
    _last_primary_dim: int | None = None

    @property
    def name(self) -> str:
        return " → ".join(p.name for p in self.providers)

    @property
    def primary(self) -> MultiKeyProvider:
        return self.providers[0]

    @property
    def _model(self) -> str | None:
        """Global model override propagated from settings (e.g. maestro_model)."""
        return self.providers[0]._model if self.providers else None

    @_model.setter
    def _model(self, value: str | None) -> None:
        # ponytail: model override is a primary-provider directive only.
        # Fallback providers use their own default models to avoid
        # sending e.g. "gpt-4o-mini" to Anthropic/Gemini.
        if self.providers:
            self.providers[0]._model = value

    @property
    def _default_heavy(self) -> bool:
        """Default heavy flag propagated from user's use_heavy_model setting."""
        return self.providers[0]._default_heavy if self.providers else False

    @_default_heavy.setter
    def _default_heavy(self, value: bool) -> None:
        for p in self.providers:
            p._default_heavy = value

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool | None = None,
        task_type: str = TaskType.DEFAULT,
    ) -> str:
        """Chat c адаптивным выбором провайдера.

        Сортирует провайдеров по композитному score (успешность + латентность)
        и пробует наиболее надёжного/быстрого первым. Embeddings не сортируются —
        остаются на primary для совместимости размерностей векторов.
        """
        last_error: Exception | None = None
        now = asyncio.get_running_loop().time()
        sorted_providers = sorted(
            self.providers,
            key=lambda p: _score_provider(p.provider_name, now),
            reverse=True,
        )
        # ── Reset LLM call budget for new user request ──
        for p in self.providers:
            p.reset_llm_budget()
        # Map None → _UNSET for MultiKeyProvider
        # (preserves "use _default_heavy" semantic)
        mkp_heavy = _UNSET if heavy is None else heavy
        for provider in sorted_providers:
            try:
                with start_span(
                    "llm.chat",
                    provider=provider.provider_name,
                    task_type=task_type,
                    msg_count=len(messages),
                ):
                    return await provider.chat(
                        messages, heavy=mkp_heavy, task_type=task_type
                    )
            except Exception as exc:
                if not isinstance(
                    exc, (ExhaustedError, AttributeError, NotImplementedError)
                ) and not _is_retryable_llm_error(exc):
                    raise
                last_error = exc
                logger.warning(
                    "LLM provider %s failed, trying next: %s",
                    provider.name,
                    safe_str(exc)[:200],
                )
        raise last_error or RuntimeError("All LLM providers failed")

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool | None = None,
        task_type: str = TaskType.DEFAULT,
    ) -> AsyncGenerator[str]:
        """Stream chat with adaptive provider fallback. Falls back to regular chat."""
        now = asyncio.get_running_loop().time()
        sorted_providers = sorted(
            self.providers,
            key=lambda p: _score_provider(p.provider_name, now),
            reverse=True,
        )
        # ── Reset LLM call budget for new user request ──
        for p in self.providers:
            p.reset_llm_budget()
        # Map None → _UNSET for MultiKeyProvider
        # (preserves "use _default_heavy" semantic)
        mkp_heavy = _UNSET if heavy is None else heavy
        for provider in sorted_providers:
            try:
                async for token in provider.chat_stream(
                    messages, heavy=mkp_heavy, task_type=task_type
                ):
                    yield token
                return
            except (AttributeError, NotImplementedError):
                continue
            except Exception as exc:
                if not isinstance(exc, ExhaustedError) and not _is_retryable_llm_error(
                    exc
                ):
                    raise
                logger.warning(
                    "LLM provider %s streaming failed, trying next: %s",
                    provider.name,
                    safe_str(exc)[:200],
                )
        # All streaming failed — fallback to regular chat
        yield await self.chat(messages, heavy=heavy, task_type=task_type)

    async def chat_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        task_type: str = TaskType.DEFAULT,
    ) -> ChatResponse:
        """Tool chat with adaptive provider fallback."""
        last_error: Exception | None = None
        now = asyncio.get_running_loop().time()
        sorted_providers = sorted(
            self.providers,
            key=lambda p: _score_provider(p.provider_name, now),
            reverse=True,
        )
        # ── Reset LLM call budget for new user request ──
        for p in self.providers:
            p.reset_llm_budget()
        for provider in sorted_providers:
            try:
                with start_span(
                    "llm.chat_with_tools",
                    provider=provider.provider_name,
                    task_type=task_type,
                    msg_count=len(messages),
                ):
                    return await provider.chat_with_tools(
                        messages, tools=tools, task_type=task_type
                    )
            except Exception as exc:
                if not isinstance(
                    exc, (ExhaustedError, AttributeError, NotImplementedError)
                ) and not _is_retryable_llm_error(exc):
                    raise
                last_error = exc
                logger.warning(
                    "LLM provider %s chat_with_tools failed, trying next: %s",
                    provider.name,
                    safe_str(exc)[:200],
                )
        raise last_error or RuntimeError("All LLM providers failed")

    async def embed(self, text: str) -> list[float]:
        """Embed с fallback по цепочке провайдеров.

        При фейле primary — пробует следующих. ВАЖНО: размерности векторов
        могут отличаться между провайдерами (BGE-M3: 1024, OpenAI: 1536).
        Fallback с несовпадающей размерностью вызывает ValueError.
        """
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                result = await provider.embed(text)
                # M8: запоминаем размерность первого успешного эмбеддинга,
                # даже если primary не сработал — для валидации размерностей.
                if self._last_primary_dim is None:
                    self._last_primary_dim = len(result)
                elif len(result) != self._last_primary_dim:
                    raise ValueError(
                        f"Embedding dimension mismatch: "
                        f"primary={self._last_primary_dim}, "
                        f"fallback {provider.name}={len(result)}. "
                        "Vectors would corrupt Qdrant index."
                    )
                return result
            except Exception as exc:
                if not isinstance(
                    exc, (ExhaustedError, NotImplementedError, ValueError)
                ) and not _is_retryable_llm_error(exc):
                    raise
                last_error = exc
                logger.warning(
                    "Embed provider %s failed, trying fallback: %s",
                    provider.name,
                    safe_str(exc)[:200],
                )
        raise last_error or RuntimeError("All embed providers failed")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed_batch с fallback по цепочке провайдеров.

        Аналогично embed() — при фейле primary пробует backup-провайдеров,
        с проверкой размерности векторов для предотвращения повреждения Qdrant.
        """
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                result = await provider.embed_batch(texts)
                if result:
                    # M8: запоминаем размерность первого успешного эмбеддинга
                    if self._last_primary_dim is None:
                        self._last_primary_dim = len(result[0])
                    elif len(result[0]) != self._last_primary_dim:
                        raise ValueError(
                            f"Embedding dimension mismatch: "
                            f"primary={self._last_primary_dim}, "
                            f"fallback {provider.name}={len(result[0])}. "
                            "Vectors would corrupt Qdrant index."
                        )
                return result
            except Exception as exc:
                if not isinstance(
                    exc, (ExhaustedError, NotImplementedError, ValueError)
                ) and not _is_retryable_llm_error(exc):
                    raise
                last_error = exc
                logger.warning(
                    "Embed_batch provider %s failed, trying fallback: %s",
                    provider.name,
                    safe_str(exc)[:200],
                )
        raise last_error or RuntimeError("All embed_batch providers failed")

    async def validate_key(self) -> bool:
        for provider in self.providers:
            if await provider.validate_key():
                return True
        return False

    async def close(self) -> None:
        """Close all child provider instances."""
        _cancelled = False
        for p in self.providers:
            if hasattr(p, "close"):
                try:
                    await p.close()
                except asyncio.CancelledError:
                    # Shield: finish closing remaining providers even if
                    # cancelled. Re-raise after all are closed.
                    if (task := asyncio.current_task()) is not None:
                        task.uncancel()
                    _cancelled = True
                except Exception:
                    logger.debug(
                        "Non-critical error closing provider %s",
                        getattr(p, "name", p),
                        exc_info=True,
                    )
        if _cancelled:
            raise asyncio.CancelledError()

    async def list_models(self) -> list[str]:
        """Возвращает только включённые (enabled) модели из всех primary-провайдеров."""
        all_models: set[str] = set()
        for provider in self.providers:
            try:
                models = await provider.list_models()
                all_models.update(models)
            except Exception:
                logger.debug("list_models failed for %s", provider.name, exc_info=True)
                continue
        return sorted(all_models)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
