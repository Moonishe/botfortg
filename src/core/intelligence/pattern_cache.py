"""Pattern Cache — кэширует успешные пары (intent, action) для обхода LLM-роутинга."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.core.cache.manager import ManagedCache, cache_manager

logger = logging.getLogger(__name__)


class PatternCache:
    """In-memory TTL-кэш успешных intent → action паттернов.

    Ключ: f"{intent_type}:{
    Значение: {"action": str, "count": int, "last_success": float, "ttl": int}

    Когда один и тот же intent + user_id накапливает >= min_count успешных
    использований, последующие запросы возвращают закэшированный action,
    минуя LLM-роутинг.
    """

    def __init__(self, default_ttl: int = 3600, min_count: int = 3):
        self._default_ttl = default_ttl
        self._min_count = min_count
        # Bounded cache: LRU eviction + TTL handled by ManagedCache
        self._cache: ManagedCache[str, dict[str, Any]] = cache_manager.register(
            ManagedCache(
                name="patterns",
                max_size=2000,
                default_ttl=float(default_ttl),
                on_evict=lambda k, v: self._stats_meta.pop(k, None),
            )
        )
        # Sidecar tracking: count + ttl per key (needed for record/invalidate logic)
        self._stats_meta: dict[str, dict[str, Any]] = {}
        # Statistics
        self._hits: int = 0
        self._misses: int = 0
        self._bypasses: int = 0  # сколько раз обошли LLM

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def record_pattern(
        self,
        user_id: int,
        intent_type: str,
        action: str,
        *,
        ttl: int | None = None,
    ) -> None:
        """Записывает успешный паттерн.

        Если пара (_type) уже существует и action совпадает —
        инкрементирует счётчик. Если action отличается — сбрасывает счётчик
        на 1 (новый паттерн).
        """
        key = self._make_key(user_id, intent_type)
        entry_ttl = ttl if ttl is not None else self._default_ttl
        now = time.monotonic()

        # Atomic read-modify-write under ManagedCache's internal lock
        async with self._cache._lock:
            raw = self._cache._cache.get(key)
            if raw is not None:
                expires_at, entry = raw
                if expires_at <= now:
                    # Expired — remove and re-create
                    self._cache._cache.pop(key, None)
                    self._cache.metrics.expirations += 1
                    raw = None

            if raw is not None:
                _, entry = raw
                if entry["action"] == action:
                    entry["count"] += 1
                    entry["last_success"] = now
                    entry["ttl"] = entry_ttl
                    logger.debug(
                        "Pattern cache: incremented %s [%s] (count=%d)",
                        key,
                        action,
                        entry["count"],
                    )
                else:
                    # Действие изменилось — сбрасываем
                    entry["action"] = action
                    entry["count"] = 1
                    entry["last_success"] = now
                    entry["ttl"] = entry_ttl
                    logger.debug(
                        "Pattern cache: reset %s [%s] (action changed)",
                        key,
                        action,
                    )
                # Refresh TTL in OrderedDict
                self._cache._cache[key] = (now + entry_ttl, entry)
                self._cache._cache.move_to_end(key)
            else:
                # New entry
                entry = {
                    "action": action,
                    "count": 1,
                    "last_success": now,
                    "ttl": entry_ttl,
                }
                # LRU eviction under lock
                while len(self._cache._cache) >= self._cache.max_size:
                    oldest_key, _ = self._cache._cache.popitem(last=False)
                    self._cache.metrics.evictions += 1
                    self._stats_meta.pop(oldest_key, None)
                self._cache._cache[key] = (now + entry_ttl, entry)
                logger.debug("Pattern cache: new entry %s [%s]", key, action)

            # Update sidecar meta
            self._stats_meta[key] = {"count": entry["count"], "action": entry["action"]}

    async def get_cached_action(
        self,
        user_id: int,
        intent_type: str,
        min_count: int | None = None,
    ) -> str | None:
        """Возвращает закэшированный action если счётчик >= порога и TTL не истёк.

        Если запись просрочена — ManagedCache автоматически удаляет её и возвращает None.
        """
        key = self._make_key(user_id, intent_type)
        entry = await self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None

        threshold = min_count if min_count is not None else self._min_count
        if entry["count"] >= threshold:
            self._hits += 1
            self._bypasses += 1
            logger.debug(
                "Pattern cache HIT: %s [%s] (count=%d, bypassing LLM)",
                key,
                entry["action"],
                entry["count"],
            )
            return entry["action"]

        self._misses += 1
        return None

    async def invalidate_pattern(self, user_id: int, intent_type: str) -> bool:
        """Инвалидирует закэшированный паттерн (например, при коррекции пользователем).

        Returns:
            True если запись была и удалена, False если не найдена.
        """
        key = self._make_key(user_id, intent_type)
        result = await self._cache.invalidate(key)
        if result:
            self._stats_meta.pop(key, None)
            logger.info("Pattern cache: invalidated %s", key)
            return True
        return False

    async def clear(self) -> None:
        """Полная очистка кэша."""
        await self._cache.clear()
        self._stats_meta.clear()
        self._hits = 0
        self._misses = 0
        self._bypasses = 0

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "entries": await self._cache.size(),
            "hits": self._hits,
            "misses": self._misses,
            "bypasses": self._bypasses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
            "min_count": self._min_count,
            "default_ttl": self._default_ttl,
        }

    def top_patterns(self, n: int = 10) -> list[dict[str, Any]]:
        """Возвращает top-N паттернов по количеству использований."""
        items = sorted(
            self._stats_meta.items(),
            key=lambda kv: kv[1]["count"],
            reverse=True,
        )
        return [
            {"key": k, "action": v["action"], "count": v["count"]} for k, v in items[:n]
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(user_id: int, intent_type: str) -> str:
        return f"{intent_type}:{user_id}"


# Глобальный синглтон
pattern_cache = PatternCache()
