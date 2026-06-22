"""Retry/backoff wrapper for Telegram send operations. Placed in core/infra to avoid
core→bot layering violations — both bot/tg_sender and core/infra/notifier import from here."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

logger = logging.getLogger(__name__)


async def send_with_retry(
    send_fn,  # async callable
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> Any:
    """Вызвать send_fn(*args, **kwargs) с retry при FloodWaitError/RateLimit.

    Обрабатывает:
    - aiogram.exceptions.TelegramRetryAfter → ждать + retry
    - aiogram.exceptions.TelegramNetworkError → retry с backoff
    - telethon.errors.FloodWaitError → ждать указанное время + retry
    """
    last_exception: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await send_fn(*args, **kwargs)
        except TelegramRetryAfter as e:
            last_exception = e
            if attempt == max_retries - 1:
                raise
            delay = max(e.retry_after, base_delay * (2**attempt))
            logger.warning(
                "Telegram 429: waiting %.1fs (attempt %d/%d)",
                delay,
                attempt + 1,
                max_retries,
            )
            await asyncio.sleep(delay)
        except TelegramNetworkError as e:
            last_exception = e
            if attempt == max_retries - 1:
                logger.exception("Telegram network error, max retries reached")
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "Telegram network error: %s, retrying in %.1fs (attempt %d/%d)",
                e,
                delay,
                attempt + 1,
                max_retries,
            )
            await asyncio.sleep(delay)
        except Exception as e:
            # Telethon FloodWaitError (optional dependency — string match)
            last_exception = e
            exc_name = type(e).__name__
            if "FloodWaitError" in exc_name:
                if attempt == max_retries - 1:
                    raise
                # ponytail: cap FloodWait at 60s — Telegram can demand 86400+ sec
                # which would block the coroutine for hours.
                raw_wait = getattr(e, "seconds", base_delay * (2**attempt))
                try:
                    raw_wait_f = float(raw_wait)
                except (TypeError, ValueError):
                    raw_wait_f = base_delay * (2**attempt)
                wait = max(0.0, min(raw_wait_f, 60.0))
                logger.warning(
                    "FloodWait %ds (capped to %.0fs, attempt %d/%d)",
                    raw_wait,
                    wait,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(wait)
                continue
            # Неизвестная ошибка — не retry
            logger.exception("Unexpected error in send_with_retry: %s", exc_name)
            raise

    raise RuntimeError(f"Send failed after {max_retries} retries") from last_exception
