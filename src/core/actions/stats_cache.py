"""Кэш memory-статистики. TTL = 5 минут, инвалидация при изменении."""

from typing import Any

from src.core.cache.manager import ManagedCache, cache_manager

_stats: ManagedCache[str, Any] = cache_manager.register(
    ManagedCache(name="stats", max_size=5000, default_ttl=300.0)
)


async def get_cached(key: str) -> Any | None:
    return await _stats.get(key)


async def set_cache(key: str, data: Any, ttl: float = 300.0) -> None:
    await _stats.set(key, data, ttl=ttl)


async def invalidate(prefix: str = "") -> None:
    """Инвалидировать кэш. Если prefix пустой — всё. Иначе по префиксу."""
    if not prefix:
        await _stats.clear()
    else:
        await _stats.invalidate_by_prefix(prefix)
