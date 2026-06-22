"""Memory Provider ABC — абстрактный бэкенд для векторной памяти.

Позволяет переключаться между Qdrant (текущий), ChromaDB, Weaviate и т.д.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.actions.vector_store import VectorStore as _VectorStore

logger = logging.getLogger(__name__)

COLLECTION = "messages"


@dataclass
class VectorHit:
    """Результат векторного поиска — упрощённая модель для провайдеров."""

    user_id: int
    peer_id: int
    text: str
    score: float


class VectorMemoryBackend(ABC):
    """Абстрактный бэкенд для хранения и поиска векторных представлений сообщений."""

    @abstractmethod
    async def upsert(
        self,
        user_id: int,
        peer_id: int,
        text: str,
        embedding: list[float],
    ) -> None:
        """Сохранить или обновить вектор сообщения."""

    @abstractmethod
    async def search(
        self,
        embedding: list[float],
        user_id: int,
        limit: int = 10,
        peer_id: int | None = None,
    ) -> list[VectorHit]:
        """Поиск ближайших векторов по эмбеддингу."""

    @abstractmethod
    async def delete(
        self,
        user_id: int,
        peer_id: int | None = None,
    ) -> int:
        """Удалить векторы пользователя (опционально в рамках peer_id).
        Возвращает количество удалённых точек."""

    @abstractmethod
    async def count(self, user_id: int) -> int:
        """Количество сохранённых векторов для пользователя."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Проверка доступности бэкенда."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Graceful shutdown бэкенда."""


class QdrantMemoryBackend(VectorMemoryBackend):
    """Адаптер к существующему VectorStore (Qdrant).

    Делегирует основные операции VectorStore, недостающие реализует напрямую
    через QdrantClient.  Упрощает сигнатуры — генерирует недостающие поля
    (message_id, date_iso) автоматически.
    """

    def __init__(self, vector_store: _VectorStore) -> None:
        from src.core.actions.vector_store import VectorStore as _VS, COLLECTION as _COL

        self._vs: _VS = vector_store
        self._client = self._vs._client
        self._collection = _COL

    # ── реализация ABC ──────────────────────────────────────────────────

    async def upsert(
        self,
        user_id: int,
        peer_id: int,
        text: str,
        embedding: list[float],
    ) -> None:
        # Генерируем стабильный message_id для дедупликации
        message_id = int(
            hashlib.sha256(f"{user_id}:{peer_id}:{text}".encode()).hexdigest()[:12],
            16,
        )
        await self._vs.upsert(
            user_id=user_id,
            peer_id=peer_id,
            peer_name=None,
            message_id=message_id,
            text=text,
            date_iso=None,
            embedding=embedding,
        )

    async def search(
        self,
        embedding: list[float],
        user_id: int,
        limit: int = 10,
        peer_id: int | None = None,
    ) -> list[VectorHit]:
        raw = await self._vs.search(
            user_id=user_id,
            embedding=embedding,
            limit=limit,
            peer_id=peer_id,
        )
        return [
            VectorHit(
                user_id=h.user_id,
                peer_id=h.peer_id,
                text=h.text,
                score=h.score,
            )
            for h in raw
        ]

    async def delete(self, user_id: int, peer_id: int | None = None) -> int:
        """Удалить точки пользователя через фильтр VectorStore.

        # ponytail: VectorStore.delete не атомарен (count → delete),
        # но в пределах asyncio.Lock защищён от гонок с upsert/search.
        """
        return await self._vs.delete(user_id=user_id, peer_id=peer_id)

    async def count(self, user_id: int) -> int:
        result = await asyncio.to_thread(
            self._client.count,
            collection_name=self._collection,
            exact=True,
        )
        return result.count

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(self._client.get_collections)
            return True
        except Exception:
            logger.exception("QdrantMemoryProvider health_check failed")
            return False

    async def shutdown(self) -> None:
        await self._vs.shutdown()
