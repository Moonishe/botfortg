"""Retry policy for LLM calls — exponential backoff with jitter.

ponytail: standalone utility, upgrade to tenacity if retry logic grows.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, TypeVar
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Retry on these HTTP status codes (rate limit + server errors).
_RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is retryable (rate limit or server error)."""
    # httpx.HTTPStatusError has response.status_code
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None and status in _RETRY_STATUS_CODES:
        return True
    # httpx.ConnectError, httpx.TimeoutException — network issues.
    exc_name = type(exc).__name__
    if exc_name in ("ConnectError", "TimeoutException", "ReadTimeout", "WriteTimeout"):
        return True
    return False


async def retry_async(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Call func with retry on rate-limit/server errors.

    Exponential backoff: base_delay * 2^attempt + random jitter.
    Max retries from settings.llm_retry_max (default 3).
    """
    max_retries = settings.llm_retry_max
    base_delay = settings.llm_retry_base_delay

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
            logger.warning(
                "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                max_retries + 1,
                delay,
                exc,
            )
            await asyncio.sleep(delay)

    # Unreachable — loop either returns or raises.
    raise last_exc  # type: ignore[misc]
