"""Unified cache manager with metrics, auto-cleanup, and smart eviction."""

import asyncio
import inspect
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, TypeVar
from collections.abc import Callable

logger = logging.getLogger(__name__)

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

        # Single-writer pattern: только один writer вычисляет значение,
        # остальные читатели ждут через asyncio.Event.
        self._writer_lock = asyncio.Lock()  # Сериализует writer'ы
        self._write_events: dict[
            K, asyncio.Event
        ] = {}  # По-ключевые события для оповещения читателей

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

    async def get_or_compute(
        self, key: K, computer: Callable[[], Any], ttl: float | None = None
    ) -> V:
        """Получить значение из кэша или вычислить его (single-writer pattern).

        **Читатели (множественные):** возвращают закэшированное значение если
        оно свежее.  Если значение отсутствует или протухло — ждут, пока
        writer вычислит новое значение (через ``asyncio.Event``).

        **Writer (один):** только ОДНА корутина выполняет ``computer()``
        для данного ключа.  Остальные корутины, которые тоже хотели
        вычислить значение, просто дожидаются результата первого writer'а.

        Отличие от :meth:`upsert`:
        - ``upsert`` использует double-checked locking — несколько writer'ов
          могут гоняться, но записывается только один результат.
        - ``get_or_compute`` использует single-writer с ``asyncio.Event`` —
          writer только ОДИН, остальные ждут и **не** вызывают ``computer()``.
          Это оптимально когда ``computer()`` — дорогой вызов (LLM, БД, сеть).

        Args:
            key: Ключ кэша.
            computer: callable, возвращающий значение (или coroutine).
                      Вызывается **только** когда ни у кого нет свежего
                      значения и нет другого writer'а для этого ключа.
            ttl: Опциональный TTL в секундах. Если None — :attr:`default_ttl`.

        Returns:
            Значение из кэша (свежее) или только что вычисленное.
        """
        # ── Fast path: cache hit (под локом для консистентности) ──
        async with self._lock:
            if key in self._cache:
                expires_at, value = self._cache[key]
                if time.monotonic() < expires_at:
                    self._cache.move_to_end(key)
                    self.metrics.hits += 1
                    return value

        # ── Получить или создать per-key Event ──
        # L1: доступ к _write_events под _lock — предотвращает состояние гонки
        # между reader'ами (параллельные вызовы get_or_compute с одним ключом).
        async with self._lock:
            if key not in self._write_events:
                self._write_events[key] = asyncio.Event()
                self._write_events[
                    key
                ].set()  # Изначально «готово» (писатель не активен)

            event = self._write_events[key]

        # ── Если другой writer уже вычисляет этот ключ — ждём ──
        if not event.is_set():
            try:
                await asyncio.wait_for(event.wait(), timeout=30.0)
            except TimeoutError:
                logger.warning(
                    "get_or_compute: reader timed out waiting for writer on key=%s, "
                    "falling through to writer path",
                    key,
                )
            # После сигнала (или таймаута) — перепроверяем кэш
            async with self._lock:
                if key in self._cache:
                    expires_at, value = self._cache[key]
                    if time.monotonic() < expires_at:
                        self._cache.move_to_end(key)
                        self.metrics.hits += 1
                        return value

        # ── Мы — writer (или значение всё ещё не готово) ──
        actual_ttl = ttl if ttl is not None else self.default_ttl

        async with self._writer_lock:
            # Double-check: другой writer мог уже вычислить, пока мы ждали _writer_lock
            async with self._lock:
                if key in self._cache:
                    expires_at, value = self._cache[key]
                    if time.monotonic() < expires_at:
                        self._cache.move_to_end(key)
                        self.metrics.hits += 1
                        return value

            # Сигнализируем: writer начал работу (читатели будут ждать)
            event.clear()
            self.metrics.misses += 1

            try:
                # Вычисляем ВНЕ лока (_writer_lock удерживается для сериализации,
                # но _lock отпущен — другие операции с кэшем не блокируются)
                result = computer()
                if asyncio.iscoroutine(result):
                    result = await result

                # Сохраняем результат
                expires_at = time.monotonic() + actual_ttl

                async with self._lock:
                    # LRU eviction если нужно
                    while len(self._cache) >= self.max_size:
                        oldest_key, (_, oldest_value) = self._cache.popitem(last=False)
                        self._expires.pop(oldest_key, None)
                        self.metrics.evictions += 1
                        if self.on_evict:
                            try:
                                self.on_evict(oldest_key, oldest_value)
                            except Exception:
                                logger.debug(
                                    "on_evict failed for %s", oldest_key, exc_info=True
                                )

                    self._cache[key] = (expires_at, result)
                    self._expires[key] = expires_at

                return result

            except Exception:
                # При ошибке — зачищаем tracking, чтобы следующий запрос
                # попробовал снова (не оставляем ключ в состоянии «пишется»)
                raise

            finally:
                # Сигнализируем: writer завершил (все читатели просыпаются)
                event.set()

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
                        logger.debug(
                            "on_evict failed for %s", oldest_key, exc_info=True
                        )

            self._cache[key] = (expires_at, value)
            self._expires[key] = expires_at

    async def invalidate(self, key: K) -> bool:
        """Manually remove key from cache. Returns True if key existed."""
        async with self._lock:
            result = self._evict(key, expired=False)
            self._write_events.pop(key, None)  # Очистка per-key event
            return result

    async def clear(self) -> int:
        """Clear all entries. Returns count of removed items."""
        async with self._lock:
            items = list(self._cache.items())
            count = len(self._cache)
            self._cache.clear()
            self._expires.clear()
            self._write_events.clear()  # Очистка всех per-key events
        for key, (_, value) in items:
            if self.on_evict:
                try:
                    result = self.on_evict(key, value)
                    if inspect.iscoroutine(result):
                        await result  # type: ignore[unreachable]
                except Exception:
                    logger.debug("on_evict failed for %s", key, exc_info=True)
        return count

    async def invalidate_by_prefix(self, prefix: str) -> int:
        """Remove all keys starting with *prefix*. Returns count of removed."""
        async with self._lock:
            keys_to_remove = [k for k in self._cache if str(k).startswith(prefix)]
            for key in keys_to_remove:
                self._evict(key, expired=False)
                self._write_events.pop(key, None)
            return len(keys_to_remove)

    async def cleanup_expired(self) -> int:
        """Remove all expired entries. Call periodically.
        Also evicts stale _write_events entries to prevent memory leaks."""
        async with self._lock:
            now = time.monotonic()
            expired_keys = [
                k for k, (expires_at, _) in self._cache.items() if expires_at <= now
            ]
            for key in expired_keys:
                self._evict(key, expired=True)
            # Cleanup stale _write_events: remove events for keys no longer in cache
            stale_events = [k for k in self._write_events if k not in self._cache]
            for key in stale_events:
                self._write_events.pop(key, None)
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

    async def upsert(
        self,
        key: K,
        ttl: float | None,
        factory: Callable[[], Any],
    ) -> tuple[Any, bool]:
        """Atomic read-modify-write. Calls factory() only if key is missing or expired.

        Uses **double-checked locking** to avoid holding the lock during slow
        factory calls (DB I/O, LLM, embedding).  The lock is released before
        calling *factory* and re-acquired for write-back, keeping all other
        cache operations unblocked during computation.

        Args:
            key: Cache key.
            ttl: TTL in seconds. Falls back to default_ttl if None.
            factory: Callable that returns (or awaits) the value to store.
                     Called only when a new value must be created.

        Returns:
            Tuple of (value, was_created).
            was_created=True  → factory was called, value is fresh.
            was_created=False → cached value returned, no factory call.
        """
        # ── Fast path under lock: check existing ──
        async with self._lock:
            if key in self._cache:
                expires_at, value = self._cache[key]
                if time.monotonic() < expires_at:
                    self._cache.move_to_end(key)
                    self.metrics.hits += 1
                    return value, False
                # H1: Don't evict yet — factory might fail.  Just note it.
                existing_expired = True
            else:
                existing_expired = False

        # ── Compute value OUTSIDE the lock ──
        # L5: счётчик misses вне лока — учитываем каждый промах,
        # даже если double-check внутри writer_lock обнаружит свежее значение.
        self.metrics.misses += 1
        result = factory()
        if asyncio.iscoroutine(result):
            result = await result

        # ── Write-back under lock (with re-check) ──
        async with self._lock:
            # H1: Factory succeeded — safe to evict the old entry now.
            if existing_expired:
                self._evict(key, expired=True)

            if key in self._cache:
                expires_at, value = self._cache[key]
                if time.monotonic() < expires_at:
                    # Someone else got there first — return their result
                    self._cache.move_to_end(key)
                    self.metrics.hits += 1  # M3: count hit for returned value
                    return value, False

            actual_ttl = ttl if ttl is not None else self.default_ttl
            expires_at = time.monotonic() + actual_ttl

            # LRU eviction if needed
            while len(self._cache) >= self.max_size:
                oldest_key, (_, oldest_value) = self._cache.popitem(last=False)
                self._expires.pop(oldest_key, None)
                self.metrics.evictions += 1
                if self.on_evict:
                    try:
                        self.on_evict(oldest_key, oldest_value)
                    except Exception:
                        logger.debug("Non-critical error", exc_info=True)

            self._cache[key] = (expires_at, result)
            self._expires[key] = expires_at
            return result, True

    def _evict(self, key: K, expired: bool) -> bool:
        """Internal eviction (must be called with lock held)."""
        if key not in self._cache:
            return False
        _, value = self._cache.pop(key)
        self._expires.pop(key, None)
        self._write_events.pop(key, None)  # Предотвращает утечку asyncio.Event
        if expired:
            self.metrics.expirations += 1
        if self.on_evict:
            try:
                self.on_evict(key, value)
            except Exception:
                logger.debug("on_evict failed for %s", key, exc_info=True)
        return True

    async def size(self) -> int:
        """Current number of cached items."""
        async with self._lock:
            return len(self._cache)

    async def stats(self) -> dict:
        """Get cache statistics."""
        async with self._lock:
            return {
                "name": self.name,
                "size": len(self._cache),
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
        stats = await cache_manager.all_stats()
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
        if self._cleanup_task is not None and not self._cleanup_task.done():
            logger.warning(
                "Background cleanup already running, skipping duplicate call"
            )
            return

        async def _cleanup_loop():
            while True:
                try:
                    await asyncio.sleep(interval)
                    await self.cleanup_all()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("cache_manager.cleanup_all() failed")

        self._cleanup_task = asyncio.create_task(_cleanup_loop())

    async def stop_background_cleanup(self) -> None:
        """Stop background cleanup task. Call at app shutdown."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def all_stats(self) -> dict[str, dict]:
        """Get statistics for all caches."""
        result = {}
        for name, cache in self._caches.items():
            result[name] = await cache.stats()
        return result

    async def reset_for_test(self) -> None:
        """Reset cache manager state for testing.

        Cancels any running background cleanup task and clears all
        registered caches, returning the manager to a clean initial state.

        Use in pytest fixtures to guarantee isolation between tests::

            @pytest.fixture
            async def cm():
                _reset_cache_manager_for_test()
                yield cache_manager
                await cache_manager.reset_for_test()
        """
        await self.stop_background_cleanup()
        self._cleanup_task = None
        caches = list(self._caches.values())
        self._caches.clear()
        for cache in caches:
            await cache.clear()


def create_cache_manager() -> CacheManager:
    """Create a new :class:`CacheManager` instance.

    Use this factory for dependency injection when you want fine-grained
    control over lifecycle (e.g. in tests or when embedding the library).

    For normal application code, prefer the module-level ``cache_manager``
    singleton which is pre-configured and ready to use.
    """
    return CacheManager()


def _reset_cache_manager_for_test() -> CacheManager:
    """Replace the global ``cache_manager`` singleton with a fresh instance.

    Returns the new instance.  Typical usage in a pytest fixture::

        @pytest.fixture
        def cache_manager():
            cm = _reset_cache_manager_for_test()
            yield cm

    .. note::
       This helper reaches into the module to swap the singleton so that
       any code importing ``cache_manager`` from
       ``src.core.cache.manager`` will see the fresh instance.
    """
    new_cm = CacheManager()
    import src.core.cache.manager as _mod

    _mod.cache_manager = new_cm
    return new_cm


# Global instance (singleton for backward compatibility)
cache_manager = CacheManager()
