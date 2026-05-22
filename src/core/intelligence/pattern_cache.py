"""Pattern Cache — кэширует успешные пары (intent, action) для обхода LLM-роутинга."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class PatternCache:
    """In-memory TTL-кэш успешных intent → action паттернов.

    Ключ: f"{intent_type}:{user_id}"
    Значение: {"action": str, "count": int, "last_success": float, "ttl": int}

    Когда один и тот же intent + user_id накапливает >= min_count успешных
    использований, последующие запросы возвращают закэшированный action,
    минуя LLM-роутинг.
    """

    def __init__(self, default_ttl: int = 3600, min_count: int = 3):
        self._cache: dict[str, dict[str, Any]] = {}
        self._default_ttl = default_ttl
        self._min_count = min_count
        self._lock = asyncio.Lock()
        # Статистика
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

        Если пара (user_id, intent_type) уже существует и action совпадает —
        инкрементирует счётчик. Если action отличается — сбрасывает счётчик
        на 1 (новый паттерн).
        """
        async with self._lock:
            key = self._make_key(user_id, intent_type)
            now = time.monotonic()
            entry_ttl = ttl if ttl is not None else self._default_ttl

            if key in self._cache:
                entry = self._cache[key]
                if entry["action"] == action:
                    entry["count"] += 1
                    entry["last_success"] = now
                    entry["ttl"] = entry_ttl
                    logger.debug(
                        "Pattern cache: incremented %s → %s (count=%d)",
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
                        "Pattern cache: reset %s → %s (action changed)",
                        key,
                        action,
                    )
            else:
                self._cache[key] = {
                    "action": action,
                    "count": 1,
                    "last_success": now,
                    "ttl": entry_ttl,
                }
                logger.debug("Pattern cache: new entry %s → %s", key, action)

    async def get_cached_action(
        self,
        user_id: int,
        intent_type: str,
        min_count: int | None = None,
    ) -> str | None:
        """Возвращает закэшированный action если счётчик >= порога и TTL не истёк.

        Если запись просрочена — удаляет её и возвращает None.
        """
        async with self._lock:
            key = self._make_key(user_id, intent_type)
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None

            now = time.monotonic()
            if now - entry["last_success"] > entry["ttl"]:
                del self._cache[key]
                logger.debug("Pattern cache: TTL expired for %s", key)
                self._misses += 1
                return None

            threshold = min_count if min_count is not None else self._min_count
            if entry["count"] >= threshold:
                self._hits += 1
                self._bypasses += 1
                logger.debug(
                    "Pattern cache HIT: %s → %s (count=%d, bypassing LLM)",
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
        async with self._lock:
            key = self._make_key(user_id, intent_type)
            if key in self._cache:
                del self._cache[key]
                logger.info("Pattern cache: invalidated %s", key)
                return True
            return False

    async def clear(self) -> None:
        """Полная очистка кэша."""
        async with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            self._bypasses = 0

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "entries": len(self._cache),
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
            self._cache.items(),
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
