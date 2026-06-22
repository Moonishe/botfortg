"""Rate limiter — token bucket per telegram_id.

ponytail: in-memory dict with periodic cleanup, upgrade to Redis if multi-process.
"""

from __future__ import annotations

import asyncio
import time

from src.config import settings

# telegram_id → (tokens, last_refill_time)
_buckets: dict[int, tuple[float, float]] = {}
_guard = asyncio.Lock()
_LAST_CLEANUP: float = 0.0
_CLEANUP_INTERVAL = 300.0  # 5 min — purge stale buckets
_STALE_TTL = 3600.0  # 1h — entry unused for this long is removed


async def check_rate_limit(telegram_id: int) -> bool:
    """Return True if request is allowed, False if rate-limited.

    Token bucket: refills at rate_limit_per_min/60 tokens per second,
    burst capacity equals rate_limit_per_min (allows short bursts).
    """
    limit = settings.rate_limit_per_min
    if limit <= 0:
        return True  # disabled

    now = time.monotonic()
    refill_rate = limit / 60.0  # tokens per second

    global _LAST_CLEANUP
    async with _guard:
        # Periodic cleanup — purge entries untouched for > _STALE_TTL.
        if now - _LAST_CLEANUP > _CLEANUP_INTERVAL:
            stale_cutoff = now - _STALE_TTL
            _buckets = {k: v for k, v in _buckets.items() if v[1] > stale_cutoff}
            _LAST_CLEANUP = now

        tokens, last = _buckets.get(telegram_id, (float(limit), now))
        # Refill tokens based on elapsed time.
        elapsed = now - last
        tokens = min(float(limit), tokens + elapsed * refill_rate)

        if tokens < 1.0:
            _buckets[telegram_id] = (tokens, now)
            return False

        tokens -= 1.0
        _buckets[telegram_id] = (tokens, now)
        return True


async def reset_user(telegram_id: int) -> None:
    """Reset rate limit for a user (admin override)."""
    async with _guard:
        _buckets.pop(telegram_id, None)
