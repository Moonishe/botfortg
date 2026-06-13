"""Базовый класс для всех LLM-провайдеров.

Выносит повторяющиеся паттерны:
- _resolve_model() — выбор лёгкой/тяжёлой модели
- _fmt_messages() — конвертация ChatMessage → OpenAI-совместимый dict
- name, api_key, _model, _embed_model — общие поля

Иерархия:
    BaseLLMProvider (ABC)
    OpenAICompatBaseMixin  — validate_key, list_models, close
        └── OpenAICompatEmbedMixin  — embed, embed_batch
            └── Конкретные провайдеры (OpenAI, DeepSeek, Mistral, ...)

    Важно: при наследовании mixin должен быть ПЕРЕД BaseLLMProvider:
        class MyProvider(OpenAICompatEmbedMixin, BaseLLMProvider):
    Это нужно, чтобы mixin-переопределения abstract-методов (close, validate_key)
    были обнаружены механизмом ABC.

AnthropicProvider и GeminiProvider — standalone, но тоже наследуют BaseLLMProvider
для общих методов (_resolve_model, _fmt_messages).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from src.llm.base import ChatMessage

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """База для всех LLM-провайдеров.

    Общая функциональность:
    - _resolve_model(heavy) — выбор модели (явная > LIGHT_MODEL / HEAVY_MODEL)
    - _fmt_messages(messages) — ChatMessage → list[dict] (OpenAI-совместимый формат)
    - name — строковой идентификатор ("openai", "gemini", ...)
    - _model / _embed_model — пользовательское переопределение модели

    Абстрактные методы (должен реализовать каждый провайдер):
    - chat() — основной вызов
    - validate_key() — проверка ключа
    - close() — закрытие HTTP-клиента
    """

    # ── Переопределить в подклассах ──────────────────────────────────
    name: str = ""
    _LIGHT_MODEL: str = ""
    _HEAVY_MODEL: str = ""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
    ) -> None:
        """Инициализация провайдера.

        Args:
            api_key: API-ключ (Bearer token для OpenAI-совместимых).
            base_url: Кастомный endpoint (None = SDK default).
            model: Явное имя модели (None = авто-выбор из _LIGHT_MODEL/_HEAVY_MODEL).
            embed_model: Имя embedding-модели (None = не используется).
        """
        self.api_key = api_key
        self._model = model
        self._embed_model = embed_model

    # ── Общие методы ──────────────────────────────────────────────────

    def _resolve_model(self, heavy: bool = False) -> str:
        """Выбор модели: явная модель > тяжёлая > лёгкая.

        Приоритет:
        1. self._model (явно задана пользователем/роутером)
        2. self._HEAVY_MODEL (если heavy=True)
        3. self._LIGHT_MODEL (по умолчанию)
        """
        return self._model or (self._HEAVY_MODEL if heavy else self._LIGHT_MODEL)

    def _fmt_messages(self, messages: list[ChatMessage]) -> list[dict]:
        """Конвертирует ChatMessage → OpenAI-совместимый формат.

        Каждый ChatMessage становится {"role": ..., "content": ...}.
        Провайдеры с нестандартным форматом (Anthropic, Gemini) переопределяют этот метод.
        """
        return [{"role": m.role, "content": m.content} for m in messages]

    # ── Абстрактные методы ────────────────────────────────────────────

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
    ) -> str:
        """Основной вызов: chat completion (не стриминг).

        Returns:
            Текст ответа модели.
        """
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
    ) -> AsyncGenerator[str]:
        """Стриминг chat completion (если поддерживается).

        Raises:
            NotImplementedError: если провайдер не поддерживает стриминг.
        """
        raise NotImplementedError("chat_stream not supported by this provider")

    @abstractmethod
    async def validate_key(self) -> bool:
        """Лёгкий запрос: валиден ли API-ключ.

        Returns:
            True если ключ рабочий.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Закрыть HTTP-клиент и освободить соединения."""
        ...

    # ── Опциональные методы ───────────────────────────────────────────

    async def embed(self, text: str) -> list[float]:
        """Embedding одного текста (если поддерживается)."""
        # NOTE: Not all providers support embedding. Router handles this via try/except.
        raise NotImplementedError(f"{self.name} does not support embeddings")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embedding батча текстов (если поддерживается)."""
        # NOTE: Not all providers support embedding. Router handles this via try/except.
        raise NotImplementedError(f"{self.name} does not support embeddings")

    async def list_models(self) -> list[str]:
        """Список доступных моделей (если поддерживается)."""
        # NOTE: Not all providers expose model listing. Router handles this via try/except.
        raise NotImplementedError(f"{self.name} does not expose model listing")
