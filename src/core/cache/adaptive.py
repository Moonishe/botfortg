"""AdaptiveTTLCache — динамически увеличивающийся TTL для часто используемых данных.

При каждом успешном get() увеличивает TTL на этот ключ (экспоненциально, до max_ttl).
Это позволяет "горячим" ключам оставаться в кэше дольше, а "холодным" — быстро
истекает по base_ttl.

Используется для:
- contact_digest (1час → 24час для частых контактов)
- pattern_cache (1час → 48час для частых паттернов)
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Callable

from src.core.cache.manager import ManagedCache, cache_manager


class AdaptiveTTLCache:
    """Кэш с адаптивным TTL, растущим при каждом успешном чтении."""

    def __init__(
        self,
        name: str,
        base_ttl: float,
        max_ttl: float,
        max_size: int = 1000,
        growth_factor: float = 2.0,
        on_evict: Callable[[Any, Any], None] | None = None,
    ):
        """Инициализация адаптивного кэша.

        Args:
            name: Имя кэша для мониторинга
            base_ttl: Базовое время жизни в секундах (минимальное)
            max_ttl: Максимальное время жизни в секундах (потолок)
            max_size: Максимальное количество ключей
            growth_factor: Множитель роста TTL (например, 2.0 = doubling)
            on_evict: Опциональный callback при eviction (key, value)
        """
        self._base_ttl = base_ttl
        self._max_ttl = max_ttl
        self._growth_factor = growth_factor
        self._ttl_map: dict[Any, float] = {}  # key → current_ttl
        self._access_counts: dict[Any, int] = {}  # key → access_count
        self._lock = asyncio.Lock()

        # Backend cache с оберткой для on_evict
        self._backend = ManagedCache(
            name=name,
            max_size=max_size,
            default_ttl=base_ttl,
            on_evict=lambda k, v: self._evict_wrapper(k, v, on_evict),
        )

        # Register for periodic cleanup
        cache_manager.register(self._backend)

    def _evict_cleanup(self, key: Any, value: Any) -> None:
        """Очистка metadata при eviction из backend."""
        self._ttl_map.pop(key, None)
        # Убираем из access_counts чтобы не было memory leak
        self._access_counts.pop(key, None)

    def _evict_wrapper(
        self, key: Any, value: Any, on_evict_callback: Callable | None = None
    ) -> None:
        """Wrapper для обеспечения вызова обоих: cleanup и external callback.

        Гарантирует что _evict_cleanup всегда вызывается, и external callback
        тоже вызывается если он передан, без short-circuit проблем.
        """
        self._evict_cleanup(key, value)
        if on_evict_callback:
            on_evict_callback(key, value)

    def _calculate_ttl(self, key: Any) -> float:
        """Рассчитать TTL для ключа на основе количества доступов."""
        accesses = self._access_counts.get(key, 0)
        if accesses == 0:
            return self._base_ttl

        # TTL = base * growth^(accesses / 10)
        # Делим на 10 чтобы рост был плавным (каждые 10 access => удвоение)
        multiplier = self._growth_factor ** (accesses / 10.0)
        ttl = self._base_ttl * multiplier
        return min(ttl, self._max_ttl)

    async def get(self, key: Any) -> Any:
        """Получить значение и увеличить TTL если ключ найден."""
        value = await self._backend.get(key)
        if value is None:
            return None

        # Calculate new TTL under lock
        async with self._lock:
            self._access_counts[key] = self._access_counts.get(key, 0) + 1
            new_ttl = self._calculate_ttl(key)
            if new_ttl != self._ttl_map.get(key):
                self._ttl_map[key] = new_ttl

        # Backend calls OUTSIDE self._lock
        current_metadata = await self._backend.get_metadata(key)
        if not current_metadata:
            async with self._lock:
                self._ttl_map.pop(key, None)
                self._access_counts.pop(key, None)
            return value

        old_ttl = current_metadata.get("ttl", 0)
        if abs(new_ttl - old_ttl) > 1.0:
            await self._backend.update_ttl(key, new_ttl)
        return value

    async def set(self, key: Any, value: Any) -> None:
        """Установить значение с базовым TTL."""
        async with self._lock:
            # Reset access count for fresh TTL calculation on overwrite or new key
            self._access_counts[key] = 0
            self._ttl_map[key] = self._base_ttl

        await self._backend.set(key, value, ttl=self._base_ttl)

    async def invalidate(self, key: Any) -> bool:
        """Удалить ключ из кэша."""
        async with self._lock:
            self._ttl_map.pop(key, None)
            self._access_counts.pop(key, None)

        return await self._backend.invalidate(key)

    async def clear(self) -> None:
        """Очистить весь кэш."""
        async with self._lock:
            self._ttl_map.clear()
            self._access_counts.clear()

        await self._backend.clear()

    async def stats(self) -> dict[str, Any]:
        """Статистика кэша с информацией об адаптивных TTL."""
        backend_stats = await self._backend.stats()

        # Calculate distribution of TTLs
        ttl_values = list(self._ttl_map.values())
        ttl_distribution = {
            "min": min(ttl_values) if ttl_values else 0,
            "max": max(ttl_values) if ttl_values else 0,
            "avg": sum(ttl_values) / len(ttl_values) if ttl_values else 0,
        }

        # Access frequency distribution
        access_values = list(self._access_counts.values())
        access_distribution = {
            "keys_with_0_access": sum(1 for v in access_values if v == 0),
            "keys_with_1_10_access": sum(1 for v in access_values if 1 <= v <= 10),
            "keys_with_10_plus_access": sum(1 for v in access_values if v > 10),
        }

        return {
            **backend_stats,
            "base_ttl": self._base_ttl,
            "max_ttl": self._max_ttl,
            "growth_factor": self._growth_factor,
            "ttl_distribution": ttl_distribution,
            "access_distribution": access_distribution,
            "adaptive": True,
        }

    async def get_access_count(self, key: Any) -> int:
        """Получить количество доступов к ключу."""
        async with self._lock:
            return self._access_counts.get(key, 0)

    async def reset_access(self, key: Any) -> None:
        """Сбросить счетчик доступов для ключа (TTL вернется к базовому)."""
        needs_update = False
        async with self._lock:
            self._access_counts[key] = 0
            if key in self._ttl_map:
                self._ttl_map[key] = self._base_ttl
                needs_update = True
        if needs_update:
            await self._backend.update_ttl(key, self._base_ttl)
