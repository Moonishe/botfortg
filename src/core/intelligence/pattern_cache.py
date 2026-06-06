"""Pattern Cache — кэширует успешные пары (intent, action) для обхода LLM-роутинга."""

from __future__ import annotations

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

        async def _factory() -> dict[str, Any]:
            return {
                "action": action,
                "count": 1,
                "last_success": now,
                "ttl": entry_ttl,
            }

        value, was_created = await self._cache.upsert(key, entry_ttl, _factory)

        if not was_created:
            # Entry already existed — check if action changed
            if value["action"] == action:
                value["count"] += 1
                value["last_success"] = now
                value["ttl"] = entry_ttl
                logger.debug(
                    "Pattern cache: incremented %s [%s] (count=%d)",
                    key,
                    action,
                    value["count"],
                )
            else:
                value["action"] = action
                value["count"] = 1
                value["last_success"] = now
                value["ttl"] = entry_ttl
                logger.debug(
                    "Pattern cache: reset %s [%s] (action changed)",
                    key,
                    action,
                )
            # Write back modified value through public API
            await self._cache.set(key, value, entry_ttl)
        else:
            logger.debug("Pattern cache: new entry %s [%s]", key, action)

        # Update sidecar meta
        self._stats_meta[key] = {"count": value["count"], "action": value["action"]}

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
        meta_snapshot = dict(self._stats_meta)
        items = sorted(
            meta_snapshot.items(),
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

    async def reset_for_test(self) -> None:
        """Reset pattern cache state for testing.

        Clears all cached patterns and resets hit/miss/bypass counters
        to zero.

        Use in pytest fixtures to guarantee isolation between tests::

            @pytest.fixture
            async def pc():
                _reset_pattern_cache_for_test()
                yield pattern_cache
                await pattern_cache.reset_for_test()
        """
        await self.clear()


def create_pattern_cache() -> PatternCache:
    """Create a new :class:`PatternCache` instance.

    Use this factory for dependency injection when you want fine-grained
    control over lifecycle (e.g. in tests or when embedding the library).

    For normal application code, prefer the module-level ``pattern_cache``
    singleton which is pre-configured and ready to use.

    .. note::
       The new instance registers its internal ``ManagedCache`` with the
       global ``cache_manager`` singleton.  If you are also creating a
       separate ``CacheManager`` for testing, call
       :func:`create_cache_manager` first and replace the global singleton
       via :func:`_reset_cache_manager_for_test`.
    """
    return PatternCache()


def _reset_pattern_cache_for_test() -> PatternCache:
    """Replace the global ``pattern_cache`` singleton with a fresh instance.

    Returns the new instance.  Typical usage in a pytest fixture::

        @pytest.fixture
        def pattern_cache():
            pc = _reset_pattern_cache_for_test()
            yield pc

    .. note::
       This helper reaches into the module to swap the singleton so that
       any code importing ``pattern_cache`` from
       ``src.core.intelligence.pattern_cache`` will see the fresh instance.
    """
    new_pc = PatternCache()
    import src.core.intelligence.pattern_cache as _mod

    _mod.pattern_cache = new_pc
    return new_pc


# Глобальный синглтон (для обратной совместимости)
pattern_cache = PatternCache()
