from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Protocol


Role = Literal["system", "user", "assistant"]


class TaskType:
    """Типы LLM-задач — определяют выбор модели и параметры генерации."""

    MAESTRO = "maestro"  # Основной orchestration (планирование, синтез)
    DRAFT = "draft"  # Черновики ответов
    MEMORY = "memory"  # Извлечение/тегирование памяти
    SEARCH = "search"  # Семантический поиск
    STT = "stt"  # Распознавание речи
    HUMANIZE = "humanize"  # Очеловечивание текста
    CLASSIFY = "classify"  # Классификация намерений
    SUMMARIZE = "summarize"  # Саммари/дайджест
    SKILLS = "skills"  # Навыки и инструменты
    BACKGROUND = "background"  # Фоновые задачи
    VISION = "vision"  # Мультимодальный анализ изображений
    GOAL_JUDGE = "goal_judge"  # Goal Judge — финальная оценка достижения цели
    DEFAULT = "default"  # Обычный чат


@dataclass
class ChatMessage:
    role: Role
    content: str


class LLMProvider(Protocol):
    @property
    def name(self) -> str:
        raise NotImplementedError

    async def validate_key(self) -> bool:
        raise NotImplementedError

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool
        | None = None,  # DEPRECATED: use task_type instead. Kept for backward compat.
        task_type: str = "default",
    ) -> str:
        raise NotImplementedError

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool
        | None = None,  # DEPRECATED: use task_type instead. Kept for backward compat.
        task_type: str = "default",
    ) -> AsyncGenerator[str]:
        """Stream tokens from chat completion.
        Raises NotImplementedError if unsupported."""
        raise NotImplementedError("chat_stream not supported by this provider")
        yield  # type: ignore[unreachable]

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        """Return available model IDs from the provider.

        Raises NotImplementedError if unsupported.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Close underlying HTTP client and release connections."""


class VisionProvider(Protocol):
    """Protocol for providers that support multimodal (image+text) inputs."""

    async def chat_with_image(
        self,
        messages: list[ChatMessage],
        image_data: bytes,
        image_mime: str = "image/jpeg",
        *,
        task_type: str = "default",
    ) -> str:
        raise NotImplementedError


class TTSProvider(Protocol):
    """Protocol for text-to-speech providers."""

    name: str

    async def validate_key(self) -> bool:
        """Validate API key with a lightweight request."""
        raise NotImplementedError

    async def synthesize(
        self, text: str, *, voice: str = "default", speed: float = 1.0
    ) -> bytes:
        """Synthesize speech from text. Returns raw audio bytes."""
        raise NotImplementedError

    async def list_voices(self) -> list[str]:
        """Return available voice IDs."""
        raise NotImplementedError

    async def close(self) -> None:
        """Close underlying HTTP client."""
