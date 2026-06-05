"""Settings cache with TTL invalidation — shared across core and bot layers.

Eliminates architecture violation: core must not depend on bot.
This module lives in src.core.infra (neutral infra layer) so both
src.core.* and src.bot.* can safely import from it.
"""

from src.core.cache.manager import ManagedCache, cache_manager

_settings_cache: ManagedCache[int, dict] = cache_manager.register(
    ManagedCache(name="settings", max_size=1000, default_ttl=60.0)
)


async def invalidate_settings_cache(telegram_id: int | None = None) -> None:
    """Сбросить кэш настроек (вызывается при изменении /settings).
    Если telegram_id=None — сбрасывает весь кэш."""
    if telegram_id is not None:
        await _settings_cache.invalidate(telegram_id)
    else:
        await _settings_cache.clear()
