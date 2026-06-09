"""TTLCache — generic async TTL-кэш (Phase 2).

Заменяет ручную реализацию TTL dict-ов в memory_recall.py, conversation_context.py
и других модулях. Потокобезопасен (asyncio.Lock), поддерживает max_size + eviction.

Использование:
    cache = TTLCache[str, RecallResult](max_size=1000, default_ttl=300)
    val = await cache.get("key")
    await cache.set("key", RecallResult(...), ttl=60)
    await cache.invalidate("key")
    await cache.clear()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """Async TTL-кэш с LRU-подобным вытеснением.

    Внутри: {key: (expires_at: float, value: V)} + asyncio.Lock.
    """

    def __init__(
        self,
        max_size: int = 1000,
        default_ttl: float = 300.0,
        on_evict: Callable[[K, V], None] | None = None,
        name: str = "ttl_cache",
    ):
        self._cache: dict[K, tuple[float, V]] = {}
        self._lock = asyncio.Lock()
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.on_evict = on_evict
        self.name = name
        # Метрики
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._expirations: int = 0

    # ---- Public API ----

    async def get(self, key: K) -> V | None:
        """Получить значение по ключу. Возвращает None если истекло или отсутствует."""
        now = time.monotonic()
        async with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            expires_at, value = self._cache[key]
            if now >= expires_at:
                del self._cache[key]
                self._expirations += 1
                self._misses += 1
                return None
            self._hits += 1
            return value

    async def set(self, key: K, value: V, ttl: float | None = None) -> None:
        """Сохранить значение. Вытесняет старейший при превышении max_size."""
        ttl = ttl if ttl is not None else self.default_ttl
        now = time.monotonic()
        async with self._lock:
            # Уже есть — обновляем
            if key in self._cache:
                self._cache[key] = (now + ttl, value)
                return
            # Вытеснение при переполнении
            if len(self._cache) >= self.max_size:
                self._evict_one(now)
            self._cache[key] = (now + ttl, value)

    async def invalidate(self, key: K) -> bool:
        """Удалить ключ из кэша. Возвращает True если ключ был."""
        async with self._lock:
            if key in self._cache:
                _expires, value = self._cache.pop(key)
                if self.on_evict:
                    try:
                        self.on_evict(key, value)
                    except Exception:
                        logger.debug(
                            "on_evict callback failed for key=%s", key, exc_info=True
                        )
                return True
            return False

    async def clear(self) -> int:
        """Очистить весь кэш. Возвращает число удалённых записей."""
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    async def get_or_set(
        self, key: K, factory: Callable[[], V], ttl: float | None = None
    ) -> V:
        """Получить из кэша или создать через factory."""
        val = await self.get(key)
        if val is None:
            val = factory()
            if asyncio.iscoroutine(val):
                val = await val
            await self.set(key, val, ttl)
        return val

    # ---- Properties ----

    @property
    def size(self) -> int:
        """Количество записей в кэше (без лока)."""
        return len(self._cache)

    @property
    def hit_rate(self) -> float:
        """Hit ratio 0..1. 0.0 при отсутствии запросов."""
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def stats(self) -> dict:
        """Метрики кэша (snapshot)."""
        return {
            "name": self.name,
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 4),
            "evictions": self._evictions,
            "expirations": self._expirations,
        }

    # ---- Internal ----

    def _evict_one(self, now: float) -> None:
        """Вытесняет одну запись: самую старую по expires_at (oldest-first)."""
        if not self._cache:
            return
        # Находим ключ с минимальным expires_at
        oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
        _expires, value = self._cache.pop(oldest_key)
        self._evictions += 1
        logger.debug(
            "TTLCache[%s] evicted key=%s (size=%d/%d)",
            self.name,
            oldest_key,
            len(self._cache),
            self.max_size,
        )
        if self.on_evict:
            try:
                self.on_evict(oldest_key, value)
            except Exception:
                logger.debug(
                    "on_evict callback failed for key=%s", oldest_key, exc_info=True
                )
