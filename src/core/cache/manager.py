"""Unified cache manager with metrics, auto-cleanup, and smart eviction."""

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


@dataclass
class CacheMetrics:
    """Cache hit/miss statistics."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate as percentage."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class ManagedCache(Generic[K, V]):
    """Thread-safe cache with TTL, LRU eviction, and metrics.

    Features:
    - Automatic expiration (TTL)
    - LRU eviction when full
    - Hit/miss tracking
    - Async-safe with locks
    - Optional cleanup callbacks

    Example:
        cache = ManagedCache(name="settings", max_size=1000, default_ttl=60.0)
        await cache.set("user_123", {"setting": "value"})
        result = await cache.get("user_123")
    """

    def __init__(
        self,
        name: str,
        max_size: int = 1000,
        default_ttl: float = 300.0,
        on_evict: Callable[[K, V], None] | None = None,
    ):
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size}")
        self.name = name
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.on_evict = on_evict
        self._cache: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._expires: dict[K, float] = {}  # expiration timestamps per key
        self._lock = asyncio.Lock()
        self.metrics = CacheMetrics()

    async def get(self, key: K) -> V | None:
        """Get value from cache. Returns None if not found or expired."""
        async with self._lock:
            if key not in self._cache:
                self.metrics.misses += 1
                return None

            expires_at, value = self._cache[key]
            if time.monotonic() >= expires_at:
                self._evict(key, expired=True)
                self.metrics.misses += 1
                return None

            # LRU: move to end on access
            self._cache.move_to_end(key)
            self.metrics.hits += 1
            return value

    async def set(self, key: K, value: V, ttl: float | None = None) -> None:
        """Set value in cache with optional TTL override."""
        ttl = ttl if ttl is not None else self.default_ttl
        expires_at = time.monotonic() + ttl

        async with self._lock:
            # Update existing
            if key in self._cache:
                self._cache[key] = (expires_at, value)
                self._expires[key] = expires_at
                self._cache.move_to_end(key)
                return

            # Evict if at capacity
            while len(self._cache) >= self.max_size:
                oldest_key, (_, oldest_value) = self._cache.popitem(last=False)
                self._expires.pop(oldest_key, None)
                self.metrics.evictions += 1
                if self.on_evict:
                    try:
                        self.on_evict(oldest_key, oldest_value)
                    except Exception:
                        pass

            self._cache[key] = (expires_at, value)
            self._expires[key] = expires_at

    async def invalidate(self, key: K) -> bool:
        """Manually remove key from cache. Returns True if key existed."""
        async with self._lock:
            return self._evict(key, expired=False)

    async def clear(self) -> int:
        """Clear all entries. Returns count of removed items."""
        async with self._lock:
            count = len(self._cache)
            for key, (_, value) in self._cache.items():
                if self.on_evict:
                    try:
                        self.on_evict(key, value)
                    except Exception:
                        pass
            self._cache.clear()
            self._expires.clear()
            return count

    async def cleanup_expired(self) -> int:
        """Remove all expired entries. Call periodically."""
        async with self._lock:
            now = time.monotonic()
            expired_keys = [
                k for k, (expires_at, _) in self._cache.items() if expires_at <= now
            ]
            for key in expired_keys:
                self._evict(key, expired=True)
            return len(expired_keys)

    async def get_metadata(self, key: K) -> dict | None:
        """Get metadata for a key.

        Returns:
            Dict with 'expires_at' timestamp and 'ttl' remaining seconds,
            or None if key doesn't exist or is expired.
        """
        async with self._lock:
            if key not in self._cache:
                return None
            expires_at = self._expires.get(key, 0)
            if expires_at > 0 and expires_at <= time.monotonic():
                self._evict(key, expired=True)
                return None  # Key expired
            remaining_ttl = max(0.0, expires_at - time.monotonic())
            return {"expires_at": expires_at, "ttl": remaining_ttl}

    async def update_ttl(self, key: K, new_ttl: float) -> bool:
        """Update TTL for a specific key.

        Args:
            key: Key to update
            new_ttl: New TTL in seconds (from now)

        Returns:
            True if key exists and was updated, False otherwise.
        """
        async with self._lock:
            if key not in self._cache:
                return False
            new_expires = time.monotonic() + new_ttl
            _, value = self._cache[key]
            self._cache[key] = (new_expires, value)
            self._expires[key] = new_expires
            self._cache.move_to_end(key)
            return True

    def _evict(self, key: K, expired: bool) -> bool:
        """Internal eviction (must be called with lock held)."""
        if key not in self._cache:
            return False
        _, value = self._cache.pop(key)
        self._expires.pop(key, None)
        if expired:
            self.metrics.expirations += 1
        if self.on_evict:
            try:
                self.on_evict(key, value)
            except Exception:
                pass
        return True

    @property
    def size(self) -> int:
        """Current number of cached items."""
        return len(self._cache)

    @property
    def stats(self) -> dict:
        """Get cache statistics."""
        return {
            "name": self.name,
            "size": self.size,
            "max_size": self.max_size,
            "hit_rate": f"{self.metrics.hit_rate:.2%}",
            "hits": self.metrics.hits,
            "misses": self.metrics.misses,
            "evictions": self.metrics.evictions,
            "expirations": self.metrics.expirations,
        }


class CacheManager:
    """Registry for all managed caches with background cleanup.

    Usage:
        // Create and register caches
        settings_cache = cache_manager.register(
            ManagedCache(name="settings", max_size=1000, default_ttl=60.0)
        )

        // Start background cleanup (call once at startup)
        await cache_manager.start_background_cleanup(interval=60.0)

        // Get stats for monitoring
        stats = cache_manager.all_stats()
    """

    def __init__(self):
        self._caches: dict[str, ManagedCache] = {}
        self._cleanup_task: asyncio.Task | None = None

    def register(self, cache: ManagedCache) -> ManagedCache:
        """Register a cache for management."""
        self._caches[cache.name] = cache
        return cache

    def get(self, name: str) -> ManagedCache | None:
        """Get cache by name."""
        return self._caches.get(name)

    async def cleanup_all(self) -> dict[str, int]:
        """Run cleanup on all caches. Returns count of expired items per cache."""
        results = {}
        for name, cache in self._caches.items():
            results[name] = await cache.cleanup_expired()
        return results

    async def start_background_cleanup(self, interval: float = 60.0) -> None:
        """Start periodic cleanup task. Call once at app startup."""

        async def _cleanup_loop():
            while True:
                await asyncio.sleep(interval)
                await self.cleanup_all()

        self._cleanup_task = asyncio.create_task(_cleanup_loop())

    async def stop_background_cleanup(self) -> None:
        """Stop background cleanup task. Call at app shutdown."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    def all_stats(self) -> dict[str, dict]:
        """Get statistics for all caches."""
        return {name: cache.stats for name, cache in self._caches.items()}


# Global instance
cache_manager = CacheManager()
