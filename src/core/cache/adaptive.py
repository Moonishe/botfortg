"""AdaptiveTTLCache — frequency-aware TTL growth.

Cache entries that are accessed frequently get progressively longer TTLs
(exponential growth), capped at max_ttl. This gives hot keys a natural
"stickiness" while cold keys expire quickly.

Uses ManagedCache as backend for thread-safety, LRU eviction, and metrics.

Usage:
    cache = AdaptiveTTLCache(
        name="contact_digest",
        base_ttl=3600.0,      # 1 hour for first access
        max_ttl=86400.0,      # 24 hours for hot keys
        growth_factor=2.0,
        max_size=500,
    )
    await cache.set("key", {"data": "value"})
    result = await cache.get("key")  # access count incremented
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypeVar

from src.core.cache.manager import ManagedCache, cache_manager

logger = logging.getLogger(__name__)

K = TypeVar("K")
V = TypeVar("V")


class AdaptiveTTLCache:
    """Cache with adaptive TTL based on access frequency.

    TTL formula: min(base_ttl * growth_factor ^ access_count, max_ttl)

    Hot keys survive longer in cache automatically. Cold keys expire
    at the base TTL and are evicted normally.

    Access counts are reset on explicit invalidation to prevent stale
    keys from growing TTL indefinitely.
    """

    def __init__(
        self,
        name: str,
        base_ttl: float = 30.0,
        max_ttl: float = 240.0,
        growth_factor: float = 2.0,
        max_size: int = 1000,
        on_evict: Any | None = None,
    ):
        self.base_ttl = base_ttl
        self.max_ttl = max_ttl
        self.growth_factor = growth_factor
        self._backend: ManagedCache = ManagedCache(
            name=name,
            max_size=max_size,
            default_ttl=base_ttl,
            on_evict=lambda k, v: (
                self._evict_cleanup(k, v) or (on_evict(k, v) if on_evict else None)
            ),
        )
        self._access_counts: dict[Any, int] = {}
        self._lock = asyncio.Lock()

        # Register with global cache manager for background cleanup
        cache_manager.register(self._backend)

    def _evict_cleanup(self, key: Any, value: Any) -> None:
        """Remove evicted key from access counts to prevent memory leak.

        Called via ManagedCache.on_evict whenever a key is evicted
        (LRU eviction, TTL expiration, explicit invalidation, or clear).
        """
        self._access_counts.pop(key, None)

    async def get(self, key: Any) -> Any | None:
        """Get value from cache.

        On cache hit: increments the access count for this key,
        extending its future TTL.
        Returns None on miss or expired entry.
        """
        value = await self._backend.get(key)
        if value is not None:
            async with self._lock:
                self._access_counts[key] = self._access_counts.get(key, 0) + 1
        return value

    async def set(
        self, key: Any, value: Any, ttl_override: float | None = None
    ) -> None:
        """Store value with adaptive TTL.

        TTL is calculated from access count:
            min(base_ttl * growth_factor ^ access_count, max_ttl)

        Use ttl_override to bypass adaptive logic (e.g. for DB-refreshed entries).
        """
        async with self._lock:
            count = self._access_counts.get(key, 0)

        if ttl_override is not None:
            ttl = ttl_override
        else:
            ttl = min(
                self.base_ttl * (self.growth_factor**count),
                self.max_ttl,
            )
            # Sanity: never go below base_ttl
            ttl = max(ttl, self.base_ttl)

        await self._backend.set(key, value, ttl=ttl)

        logger.debug(
            "AdaptiveTTLCache[%s] set key=%s count=%d ttl=%.1fs",
            self._backend.name,
            key,
            count,
            ttl,
        )

    async def invalidate(self, key: Any) -> bool:
        """Remove key and reset its access count.

        Returns True if the key existed and was removed.
        """
        result = await self._backend.invalidate(key)
        async with self._lock:
            self._access_counts.pop(key, None)
        return result

    async def clear(self) -> int:
        """Clear all entries and reset all access counts."""
        count = await self._backend.clear()
        async with self._lock:
            self._access_counts.clear()
        return count

    def get_access_count(self, key: Any) -> int:
        """Return current access count for a key (non-blocking)."""
        return self._access_counts.get(key, 0)

    def get_all_keys(self) -> list[Any]:
        """Return all currently tracked keys (snapshot, non-blocking)."""
        return list(self._access_counts.keys())

    @property
    def size(self) -> int:
        """Current number of cached items."""
        return self._backend.size

    @property
    def stats(self) -> dict[str, Any]:
        """Cache statistics including adaptive TTL info."""
        return {
            **self._backend.stats,
            "base_ttl": self.base_ttl,
            "max_ttl": self.max_ttl,
            "growth_factor": self.growth_factor,
            "tracked_keys": len(self._access_counts),
        }

    def __repr__(self) -> str:
        return (
            f"AdaptiveTTLCache(name={self._backend.name!r}, "
            f"base_ttl={self.base_ttl}, max_ttl={self.max_ttl}, "
            f"growth={self.growth_factor}, size={self.size})"
        )
