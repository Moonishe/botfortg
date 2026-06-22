"""Rate limiter — token bucket per telegram_id.

ponytail: in-memory dict, upgrade to Redis if multi-process scaling needed.
"""

from __future__ import annotations

import asyncio
import time

from src.config import settings

# telegram_id → (tokens, last_refill_time)
_buckets: dict[int, tuple[float, float]] = {}
_guard = asyncio.Lock()


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

    async with _guard:
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
