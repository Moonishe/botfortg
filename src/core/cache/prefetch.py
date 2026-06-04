"""PredictivePrefetch — access-pattern analysis and background warmup.

Tracks cache accesses across the application and identifies "hot" keys
that are likely to be requested again soon. At startup (or periodically),
calls registered warmup callbacks to pre-populate caches.

The access log is a sliding window (deque with maxlen) — old accesses
naturally fall off, so the predictor adapts to changing usage patterns.

Usage:
    from src.core.cache.prefetch import prefetch_tracker

    # Record accesses (sync, cheap, no side effects)
    prefetch_tracker.record_access("contact_digest", peer_id)
    prefetch_tracker.record_access("recall", cache_key)

    # Register warmup callbacks
    prefetch_tracker.register_warmup("contact_digest", _warmup_digest)

    # At startup: prefetch top-5 predicted keys
    await prefetch_tracker.prefetch_predictions("contact_digest", top_n=5)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Type alias for warmup callbacks: async fn(key) -> None
WarmupCallback = Callable[[Any], Awaitable[None]]


class PredictivePrefetch:
    """Tracks access patterns and prefetches probable next items.

    The predictor keeps a sliding window of recent accesses and uses
    frequency analysis to identify "hot" keys that should be
    pre-warmed in cache.

    Thread-safety: record_access() is sync and O(1). The deque
    append is atomic for single-item operations in CPython.
    prefetch_predictions() is async and safe to call from multiple
    tasks (it serializes via lock).
    """

    def __init__(self, window_size: int = 100):
        self.access_log: deque[tuple[str, Any, float]] = deque(maxlen=window_size)
        self.window_size = window_size
        self._warmup_callbacks: dict[str, WarmupCallback] = {}
        self._lock = asyncio.Lock()
        self._prefetch_stats: dict[str, int] = {}

    def register_warmup(self, cache_name: str, callback: WarmupCallback) -> None:
        """Register an async warmup callback for a named cache.

        The callback receives a single key and should fetch/warm
        the corresponding cache entry. Errors are caught and logged,
        never propagated.
        """
        self._warmup_callbacks[cache_name] = callback
        logger.debug("Registered prefetch warmup for %s", cache_name)

    def record_access(self, cache_name: str, key: Any) -> None:
        """Record a cache access event (sync, non-blocking).

        Safe to call from any context — just appends to the deque.
        Old entries are automatically evicted by the deque maxlen.
        """
        self.access_log.append((cache_name, key, time.monotonic()))

    def get_top_keys(self, cache_name: str, top_n: int = 5) -> list[Any]:
        """Get the top-N most frequently accessed keys for a cache.

        Uses frequency counting over the sliding window.
        Non-blocking, safe to call synchronously.
        """
        counts: Counter = Counter()
        for name, key, _ts in self.access_log:
            if name == cache_name:
                counts[key] += 1
        return [k for k, _v in counts.most_common(top_n)]

    def get_recent_keys(
        self, cache_name: str, n: int = 10, max_age_sec: float = 3600.0
    ) -> list[Any]:
        """Get recently accessed keys (within max_age_sec), deduplicated.

        Returns up to n unique keys, most-recent first.
        Useful for "recently viewed" prefetch strategies.
        """
        now = time.monotonic()
        seen: set = set()
        result: list[Any] = []
        # Iterate in reverse (most recent first)
        for name, key, ts in reversed(self.access_log):
            if name != cache_name:
                continue
            if now - ts > max_age_sec:
                break
            if key not in seen:
                seen.add(key)
                result.append(key)
                if len(result) >= n:
                    break
        return result

    async def prefetch_predictions(self, cache_name: str, top_n: int = 5) -> int:
        """Analyze access patterns and warm the top predicted keys.

        Calls the registered warmup callback for each top key.
        Errors are caught and logged — a single warmup failure
        never prevents other keys from being prefetched.

        Returns the number of successfully prefetched keys.
        """
        callback = self._warmup_callbacks.get(cache_name)
        if callback is None:
            logger.debug(
                "No warmup callback registered for %s — skipping prefetch",
                cache_name,
            )
            return 0

        top_keys = self.get_top_keys(cache_name, top_n)
        if not top_keys:
            logger.debug("No access history for %s — nothing to prefetch", cache_name)
            return 0

        prefetched = 0
        async with self._lock:
            for key in top_keys:
                try:
                    await callback(key)
                    prefetched += 1
                except Exception:
                    logger.debug(
                        "Prefetch warmup failed for %s key=%s",
                        cache_name,
                        key,
                        exc_info=True,
                    )
            self._prefetch_stats[cache_name] = (
                self._prefetch_stats.get(cache_name, 0) + prefetched
            )

        logger.info(
            "Prefetch for %s: %d/%d keys warmed successfully",
            cache_name,
            prefetched,
            len(top_keys),
        )
        return prefetched

    async def prefetch_recent(
        self, cache_name: str, n: int = 10, max_age_sec: float = 3600.0
    ) -> int:
        """Warm recently accessed keys (not frequency-based, recency-based).

        Useful for startup: warm up what the user was doing before restart.
        """
        callback = self._warmup_callbacks.get(cache_name)
        if callback is None:
            return 0

        recent_keys = self.get_recent_keys(cache_name, n, max_age_sec)
        if not recent_keys:
            return 0

        prefetched = 0
        async with self._lock:
            for key in recent_keys:
                try:
                    await callback(key)
                    prefetched += 1
                except Exception:
                    logger.debug(
                        "Recent prefetch failed for %s key=%s",
                        cache_name,
                        key,
                        exc_info=True,
                    )

        logger.info(
            "Recent prefetch for %s: %d/%d keys warmed",
            cache_name,
            prefetched,
            len(recent_keys),
        )
        return prefetched

    @property
    def stats(self) -> dict[str, Any]:
        """Predictor statistics."""
        return {
            "window_size": self.window_size,
            "logged_accesses": len(self.access_log),
            "registered_caches": list(self._warmup_callbacks.keys()),
            "total_prefetched": self._prefetch_stats,
        }

    def clear(self) -> None:
        """Clear all access logs (e.g. on config change)."""
        self.access_log.clear()
        self._prefetch_stats.clear()


# ─── Global singleton ────────────────────────────────────────────────────
#
# Single instance shared across all modules. Import and use directly:
#     from src.core.cache.prefetch import prefetch_tracker
#
prefetch_tracker = PredictivePrefetch(window_size=200)
