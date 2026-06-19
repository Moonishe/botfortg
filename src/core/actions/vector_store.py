"""Qdrant embedded в data/qdrant. Коллекции НЕ пересоздаются автоматически при
изменении размерности эмбеддинга — устанавливается флаг reindex_required.
Явный reindex через /index команду (reindex_collection)."""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, UTC

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from src.config import settings


logger = logging.getLogger(__name__)


COLLECTION = "messages"
MEMORY_COLLECTION = "memory_facts"


@dataclass
class VectorHit:
    user_id: int
    peer_id: int
    peer_name: str | None
    message_id: int
    text: str
    date_iso: str | None
    score: float


class VectorStore:
    def __init__(self) -> None:
        import os

        qdrant_url = os.environ.get("QDRANT_URL", "").strip()
        if qdrant_url:
            # Server mode — подключение к отдельному Qdrant инстансу
            self._client = QdrantClient(url=qdrant_url)
            logger.info("Qdrant server mode: %s", qdrant_url)
        else:
            # Embedded mode — локальное хранилище в data/qdrant
            path = settings.data_dir / "qdrant"
            path.mkdir(parents=True, exist_ok=True)
            try:
                self._client = QdrantClient(path=str(path))
            except Exception as e:
                if "lock" in str(e).lower() or "already" in str(e).lower():
                    logger.critical(
                        "Qdrant embedded lock conflict — другой процесс уже "
                        "держит блокировку. Для multi-process установите "
                        "QDRANT_URL (например QDRANT_URL=http://localhost:6333) "
                        "и запустите Qdrant сервер отдельно. "
                        "Оригинальная ошибка: %s",
                        e,
                    )
                    raise RuntimeError(
                        "Qdrant embedded lock conflict. "
                        "Set QDRANT_URL env var for server mode."
                    ) from e
                raise
            logger.debug("Qdrant embedded mode: %s", path)
        self._lock = asyncio.Lock()
        self._dim: int | None = None
        self._memory_dim: int | None = None
        self._reindex_required: bool = False
        self._memory_reindex_required: bool = False
        self._index_status: str = "unknown"
        self._indexed_at: str | None = None
        self.embedding_provider: str | None = None
        self.embedding_model: str | None = None

    async def _ensure_collection_for(
        self, dim: int, attr: str, reindex_attr: str, collection_name: str
    ) -> None:
        """Ensure a Qdrant collection exists with the correct dimension.

        Args:
            dim: Expected embedding dimension.
            attr: Instance attribute name for cached dim (e.g. ``_dim``).
            reindex_attr: Instance attribute name for reindex flag.
            collection_name: Qdrant collection name.
        """
        current_dim: int | None = getattr(self, attr)
        if current_dim == dim:
            setattr(self, reindex_attr, False)
            return
        async with self._lock:
            if getattr(self, attr) == dim:
                setattr(self, reindex_attr, False)
                return

            def _check_or_create() -> bool:
                existing = {c.name for c in self._client.get_collections().collections}
                if collection_name in existing:
                    info = self._client.get_collection(collection_name)
                    actual = info.config.params.vectors.size
                    if actual != dim:
                        logger.warning(
                            "Dimension mismatch for %s: has dim %d, "
                            "requested dim %d — reindex required. "
                            "Data NOT deleted. "
                            "Call reindex for %s explicitly.",
                            collection_name,
                            actual,
                            dim,
                            collection_name,
                        )
                        return False
                else:
                    self._client.create_collection(
                        collection_name,
                        vectors_config=qmodels.VectorParams(
                            size=dim, distance=qmodels.Distance.COSINE
                        ),
                    )
                return True

            ready = await asyncio.to_thread(_check_or_create)
            if ready:
                setattr(self, attr, dim)
                setattr(self, reindex_attr, False)
                self._index_status = "ready"
            else:
                setattr(self, reindex_attr, True)
                self._index_status = "reindex_required"

    async def _ensure_collection(self, dim: int) -> None:
        await self._ensure_collection_for(dim, "_dim", "_reindex_required", COLLECTION)

    async def _ensure_memory_collection(self, dim: int) -> None:
        await self._ensure_collection_for(
            dim, "_memory_dim", "_memory_reindex_required", MEMORY_COLLECTION
        )

    async def _detect_collection_dim(self, collection_name: str) -> int | None:
        """Detect vector dimension of an existing Qdrant collection."""

        def _do() -> int | None:
            existing = {c.name for c in self._client.get_collections().collections}
            if collection_name in existing:
                info = self._client.get_collection(collection_name)
                return info.config.params.vectors.size
            return None

        return await asyncio.to_thread(_do)

    async def upsert_memory(
        self,
        *,
        memory_id: int,
        user_id: int,
        contact_id: int | None,
        fact: str,
        embedding: list[float],
        importance: float = 0.5,
        confidence: float = 0.5,
        created_at: str | None = None,
        payload_type: str = "fact",
    ) -> None:
        """Сохраняет эмбеддинг факта памяти в коллекцию memory_facts."""
        await self._ensure_memory_collection(len(embedding))
        if self._memory_reindex_required:
            logger.warning(
                "Skipping memory upsert — %s has mismatched dimensions, "
                "call reindex_memory_collection(%d) first",
                MEMORY_COLLECTION,
                len(embedding),
            )
            return

        def _do() -> None:
            self._client.upsert(
                collection_name=MEMORY_COLLECTION,
                points=[
                    qmodels.PointStruct(
                        id=memory_id,
                        vector=embedding,
                        payload={
                            "user_id": user_id,
                            "contact_id": contact_id,
                            "fact": fact,
                            "memory_id": memory_id,
                            "importance": importance,
                            "confidence": confidence,
                            "created_at": created_at,
                            "payload_type": payload_type,
                        },
                    )
                ],
            )

        async with self._lock:
            await asyncio.to_thread(_do)

    async def delete_memories(self, memory_ids: list[int]) -> int:
        """Delete points from the ``memory_facts`` collection by memory id.

        Non-existing ids are silently ignored by Qdrant. Returns the number
        of ids submitted for deletion (actual deletion count is not exposed).
        """
        if not memory_ids:
            return 0

        def _do() -> None:
            self._client.delete(
                collection_name=MEMORY_COLLECTION,
                points_selector=qmodels.PointIdsList(points=memory_ids),
            )

        async with self._lock:
            await asyncio.to_thread(_do)
        return len(memory_ids)

    async def search_similar_memories(
        self,
        *,
        user_id: int,
        embedding: list[float],
        threshold: float = 0.85,
        limit: int = 5,
        contact_id: int | None = None,
        with_vectors: bool = False,
        payload_type: str | None = None,
    ) -> list[dict]:
        """Поиск похожих фактов в коллекции memory_facts по cosine similarity.

        Если contact_id передан — возвращаются только факты о контакте или общие
        (contact_id == null).

        Args:
            with_vectors: If True, include the Qdrant vector in the result
                (needed by callers that do MMR re-ranking). Defaults to False
                to avoid transferring unused vector payloads.
            payload_type: If not None, filter results by payload_type.
        """
        await self._ensure_memory_collection(len(embedding))
        if self._memory_reindex_required:
            logger.warning(
                "Skipping memory search in %s — reindex required",
                MEMORY_COLLECTION,
            )
            return []
        if self._memory_dim is None:
            # Edge case: collection exists but we couldn't determine dim
            async with self._lock:
                if self._memory_dim is None:
                    dim_detected = await self._detect_collection_dim(MEMORY_COLLECTION)
                    if dim_detected is None:
                        return []
                    self._memory_dim = dim_detected
        if len(embedding) != self._memory_dim:
            logger.warning(
                "Memory embedding dim %d != collection dim %d — re-index needed?",
                len(embedding),
                self._memory_dim,
            )
            return []

        flt = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="user_id", match=qmodels.MatchValue(value=user_id)
                )
            ]
        )
        if contact_id is not None:
            flt.should = [
                qmodels.FieldCondition(
                    key="contact_id", match=qmodels.MatchValue(value=contact_id)
                ),
                qmodels.FieldCondition(key="contact_id", is_null=True),
            ]
        if payload_type is not None:
            flt.must.append(
                qmodels.FieldCondition(
                    key="payload_type", match=qmodels.MatchValue(value=payload_type)
                )
            )

        def _do() -> list[qmodels.ScoredPoint]:
            response = self._client.query_points(
                collection_name=MEMORY_COLLECTION,
                query=embedding,
                limit=limit,
                query_filter=flt,
                score_threshold=threshold,
                with_vectors=with_vectors,
            )
            return response.points

        raw = await asyncio.to_thread(_do)
        results = []
        for p in raw:
            item = {
                "memory_id": p.payload.get("memory_id"),
                "fact": p.payload.get("fact", ""),
                "score": float(p.score),
                "contact_id": p.payload.get("contact_id"),
                "importance": p.payload.get("importance", 0.5),
                "confidence": p.payload.get("confidence", 0.5),
                "created_at": p.payload.get("created_at"),
            }
            if with_vectors:
                item["embedding"] = p.vector if isinstance(p.vector, list) else None
            results.append(item)
        return results

    @staticmethod
    def _point_id(user_id: int, peer_id: int, message_id: int) -> int:
        return int(
            hashlib.md5(f"{user_id}:{peer_id}:{message_id}".encode()).hexdigest()[:16],
            16,
        )

    async def upsert(
        self,
        *,
        user_id: int,
        peer_id: int,
        peer_name: str | None,
        message_id: int,
        text: str,
        date_iso: str | None,
        embedding: list[float],
    ) -> None:
        await self._ensure_collection(len(embedding))
        if self._reindex_required:
            logger.warning(
                "Skipping upsert to %s — mismatched dimensions, "
                "call reindex_collection(%d) first",
                COLLECTION,
                len(embedding),
            )
            return

        def _do() -> None:
            self._client.upsert(
                collection_name=COLLECTION,
                points=[
                    qmodels.PointStruct(
                        id=self._point_id(user_id, peer_id, message_id),
                        vector=embedding,
                        payload={
                            "user_id": user_id,
                            "peer_id": peer_id,
                            "peer_name": peer_name,
                            "message_id": message_id,
                            "text": text,
                            "date_iso": date_iso,
                        },
                    )
                ],
            )

        async with self._lock:
            await asyncio.to_thread(_do)

    async def upsert_batch(
        self,
        *,
        points: list[dict],
    ) -> None:
        """Batch upsert many points into Qdrant in a single call.

        Each dict must contain: user_id, peer_id, peer_name, message_id,
        text, date_iso, embedding.
        """
        if not points:
            return
        first = points[0]
        dim = len(first["embedding"])
        await self._ensure_collection(dim)
        if self._reindex_required:
            logger.warning("Skipping batch upsert — reindex required")
            return

        qdrant_points = [
            qmodels.PointStruct(
                id=self._point_id(p["user_id"], p["peer_id"], p["message_id"]),
                vector=p["embedding"],
                payload={
                    k: p[k]
                    for k in (
                        "user_id",
                        "peer_id",
                        "peer_name",
                        "message_id",
                        "text",
                        "date_iso",
                    )
                    if k in p
                },
            )
            for p in points
        ]

        def _do() -> None:
            self._client.upsert(collection_name=COLLECTION, points=qdrant_points)

        async with self._lock:
            await asyncio.to_thread(_do)

    async def search(
        self,
        *,
        user_id: int,
        embedding: list[float],
        limit: int = 10,
        peer_id: int | None = None,
    ) -> list[VectorHit]:
        await self._ensure_collection(len(embedding))
        if self._reindex_required:
            logger.warning(
                "Skipping search in %s — reindex required",
                COLLECTION,
            )
            return []
        if self._dim is None:
            # Edge case: collection exists but we couldn't determine dim
            async with self._lock:
                if self._dim is None:
                    dim_detected = await self._detect_collection_dim(COLLECTION)
                    if dim_detected is None:
                        return []
                    self._dim = dim_detected
        if len(embedding) != self._dim:
            logger.warning(
                "Embedding dim %d != collection dim %d — re-index needed?",
                len(embedding),
                self._dim,
            )
            return []
        flt = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="user_id", match=qmodels.MatchValue(value=user_id)
                )
            ]
        )
        if peer_id is not None:
            flt.must.append(
                qmodels.FieldCondition(
                    key="peer_id", match=qmodels.MatchValue(value=peer_id)
                )
            )

        def _do() -> list[qmodels.ScoredPoint]:
            response = self._client.query_points(
                collection_name=COLLECTION,
                query=embedding,
                limit=limit,
                query_filter=flt,
            )
            return response.points

        raw = await asyncio.to_thread(_do)
        return [
            VectorHit(
                user_id=p.payload.get("user_id"),
                peer_id=p.payload.get("peer_id"),
                peer_name=p.payload.get("peer_name"),
                message_id=p.payload.get("message_id"),
                text=p.payload.get("text", ""),
                date_iso=p.payload.get("date_iso"),
                score=float(p.score),
            )
            for p in raw
        ]

    async def reindex_collection(
        self, dim: int, *, provider: str = "", model: str = ""
    ) -> None:
        """Explicitly drop and recreate the 'messages' collection.
        Use ONLY from /index command — this DELETES all existing vectors.
        """
        async with self._lock:

            def _recreate() -> None:
                self._client.delete_collection(COLLECTION)
                self._client.create_collection(
                    COLLECTION,
                    vectors_config=qmodels.VectorParams(
                        size=dim, distance=qmodels.Distance.COSINE
                    ),
                )

            await asyncio.to_thread(_recreate)
            self._dim = dim
            self._reindex_required = False
            self._index_status = "ready"
            self._indexed_at = datetime.now(UTC).isoformat()
            if provider:
                self.embedding_provider = provider
            if model:
                self.embedding_model = model
            logger.warning(
                "%s recreated with dim %d — re-indexing needed",
                COLLECTION,
                dim,
            )

    async def reindex_memory_collection(
        self, dim: int, *, provider: str = "", model: str = ""
    ) -> None:
        """Explicitly drop and recreate the 'memory_facts' collection.
        Use ONLY from /index command — this DELETES all existing vectors.
        """
        async with self._lock:

            def _recreate() -> None:
                self._client.delete_collection(MEMORY_COLLECTION)
                self._client.create_collection(
                    MEMORY_COLLECTION,
                    vectors_config=qmodels.VectorParams(
                        size=dim, distance=qmodels.Distance.COSINE
                    ),
                )

            await asyncio.to_thread(_recreate)
            self._memory_dim = dim
            self._memory_reindex_required = False
            self._index_status = "ready"
            self._indexed_at = datetime.now(UTC).isoformat()
            if provider:
                self.embedding_provider = provider
            if model:
                self.embedding_model = model
            logger.warning(
                "%s recreated with dim %d — re-indexing needed",
                MEMORY_COLLECTION,
                dim,
            )

    async def check_health_and_recover(self) -> bool:
        """Проверяет целостность Qdrant и восстанавливается при повреждении.
        Возвращает True если здоров, False если восстановился.

        WARNING: Recovery destroys ALL vector data. Only triggered for
        persistent corruption (not transient failures).

        NOTE: Полная очистка векторных данных при неисправимом повреждении —
        by design. Векторы можно переиндексировать через /index команду.
        Исходные данные (сообщения) хранятся в SQLite и не теряются.
        Повреждение векторов не затрагивает БД сообщений.
        """
        # All Qdrant client + filesystem calls below are SYNCHRONOUS and can
        # block the event loop for the whole duration of disk I/O (rmtree on
        # a large index can take minutes). Everything is offloaded to a
        # worker thread via asyncio.to_thread.
        try:
            await asyncio.to_thread(self._client.get_collections)
            return True
        except Exception:
            logger.exception("Qdrant health check failed")

            # Try a simple reconnect first (transient failure?)
            try:
                qdrant_dir = settings.data_dir / "qdrant"
                async with self._lock:
                    await asyncio.to_thread(self._reconnect_client, qdrant_dir)
                await asyncio.to_thread(self._client.get_collections)
                logger.info("Qdrant reconnected successfully")
                return True
            except Exception:
                logger.error("Qdrant reconnect failed — storage may be corrupted")

            # CORRUPTION: only recovery path
            # Notify owner before wiping
            try:
                from src.core.scheduling.notification_queue import notification_queue

                await notification_queue.enqueue(
                    topic="system",
                    text=(
                        "⚠️ Qdrant повреждён, векторный индекс сброшен. "
                        "Векторный поиск: 0 записей проиндексировано. "
                        "Запусти /index для восстановления."
                    ),
                    priority=1,
                )
            except Exception:
                logger.debug("Non-critical error", exc_info=True)

            try:
                import shutil
                import time as _time

                qdrant_dir = settings.data_dir / "qdrant"
                # Backup before wipe — prevents total data loss on false-positive
                # corruption detection. Backup kept for 7 days, auto-cleaned.
                _backup_dir = settings.data_dir / f"qdrant.backup.{int(_time.time())}"
                try:
                    await asyncio.to_thread(
                        shutil.copytree, str(qdrant_dir), str(_backup_dir)
                    )
                    logger.warning(
                        "Qdrant backup saved to %s before recovery", _backup_dir
                    )
                except Exception:
                    logger.exception(
                        "Qdrant backup failed — proceeding with wipe anyway"
                    )

                known_dim = self._dim or settings.embedding_dim
                async with self._lock:
                    await asyncio.to_thread(self._wipe_and_rebuild, qdrant_dir)
                    await asyncio.to_thread(self._create_collection_with_dim, known_dim)
                    self._dim = known_dim
                    self._reindex_required = False
                    self._index_status = "ready"
                logger.warning("Qdrant recovered — old data lost, re-index needed")
                from src.core.scheduling.notification_queue import notification_queue
                from src.db.models import Notification

                try:
                    await notification_queue.enqueue(
                        topic="qdrant_health",
                        text=(
                            "⚠️ Qdrant был повреждён и восстановлен. "
                            "Нужен /index для переиндексации."
                        ),
                        priority=Notification.PRIORITY_HIGH,
                    )
                except Exception:
                    logger.debug("Non-critical error", exc_info=True)
                return False
            except Exception:
                logger.exception("Qdrant recovery failed")
                return False

    # ── Sync helpers for check_health_and_recover (offloaded via to_thread) ──
    # These run on the default executor — never call them directly from an
    # async function, always wrap with ``await asyncio.to_thread(...)``.

    def _reconnect_client(self, qdrant_dir) -> None:
        """Sync: close current client and open a fresh one (no reindex)."""
        self._client.close()
        self._client = QdrantClient(path=str(qdrant_dir))

    def _wipe_and_rebuild(self, qdrant_dir) -> None:
        """Sync: close, wipe storage dir, recreate empty dir + fresh client."""
        import shutil

        self._client.close()
        shutil.rmtree(str(qdrant_dir), ignore_errors=True)
        qdrant_dir.mkdir(parents=True, exist_ok=True)
        self._client = QdrantClient(path=str(qdrant_dir))

    def _create_collection_with_dim(self, known_dim: int) -> None:
        """Sync: create the main collection with the given vector dimension."""
        self._client.create_collection(
            COLLECTION,
            vectors_config=qmodels.VectorParams(
                size=known_dim, distance=qmodels.Distance.COSINE
            ),
        )

    @staticmethod
    async def cleanup_old_backups() -> int:
        """Remove Qdrant backup directories older than 7 days.

        Called at startup and periodically (every 24h) to prevent disk
        exhaustion from repeated corruption-recovery backups.

        Returns:
            Number of removed backup directories.
        """
        import shutil
        import time as _time

        data_dir = settings.data_dir
        cutoff = _time.time() - 7 * 86400  # 7 days ago
        removed = 0

        try:
            for entry in data_dir.iterdir():
                if not entry.is_dir():
                    continue
                if not entry.name.startswith("qdrant.backup."):
                    continue
                # Extract timestamp from directory name: qdrant.backup.<timestamp>
                try:
                    ts_str = entry.name.split("qdrant.backup.", 1)[1]
                    ts = int(ts_str)
                except (IndexError, ValueError):
                    continue
                if ts < cutoff:
                    shutil.rmtree(str(entry), ignore_errors=True)
                    removed += 1
                    logger.info("Removed stale Qdrant backup: %s", entry.name)
        except OSError:
            logger.debug("Non-critical error during backup cleanup", exc_info=True)

        return removed

    async def shutdown(self) -> None:
        """Graceful shutdown — закрывает Qdrant клиент."""
        try:
            if self._client:
                self._client.close()
        except Exception:
            logger.exception("vector_store shutdown failed")


_vector_store: VectorStore | None = None
_vector_store_lock = asyncio.Lock()  # async-безопасная блокировка инициализации


async def get_vector_store() -> VectorStore:
    """Возвращает синглтон VectorStore с async-безопасной инициализацией.

    Использует double-checked locking с asyncio.Lock() для предотвращения
    создания двух экземпляров QdrantClient при параллельных вызовах.
    """
    global _vector_store
    if _vector_store is not None:
        return _vector_store
    async with _vector_store_lock:
        if _vector_store is None:  # double-checked locking
            _vector_store = VectorStore()
    return _vector_store
