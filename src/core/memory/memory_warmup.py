"""Warmup scheduler for memory extraction — accelerates extraction after cold start.

After idle timeout, extraction count resets to 0 (warmup phase).
During warmup: extract from ALL contacts (aggressive).
After warmup completion: normal behavior (top-N contacts, configurable).

The extraction_interval doubles on each extraction during warmup,
matching the TencentDB-Agent-Memory warmup pattern.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# { telegram_id: (last_extraction_at_monotonic, extraction_count) }
_warmup_state: dict[int, tuple[float, int]] = {}


def should_full_extract(telegram_id: int, *, idle_timeout_sec: int = 86400) -> bool:
    """Return True if warmup is active — extract ALL contacts, not just top-N.

    Cold start = no extraction in the last `idle_timeout_sec` seconds.
    First extraction after cold start triggers full warmup.
    After that, returns to normal (False).
    """
    now = time.monotonic()
    state = _warmup_state.get(telegram_id)

    if state is None:
        # First extraction ever — warmup phase
        _warmup_state[telegram_id] = (now, 1)
        logger.debug("Warmup started for user %d (first extraction)", telegram_id)
        return True

    last_ts, count = state
    elapsed = now - last_ts

    if elapsed > idle_timeout_sec:
        # Idle timeout — reset to warmup
        _warmup_state[telegram_id] = (now, 1)
        logger.debug(
            "Warmup reset for user %d (idle %.0fs > timeout)",
            telegram_id,
            elapsed,
        )
        return True

    # Normal operation — no warmup needed
    _warmup_state[telegram_id] = (now, count + 1)
    return False


def reset_warmup(telegram_id: int) -> None:
    """Force reset warmup state for a user (e.g., after manual full extract)."""
    _warmup_state.pop(telegram_id, None)
    logger.debug("Warmup forcibly reset for user %d", telegram_id)


def get_warmup_count(telegram_id: int) -> int:
    """Return current warmup extraction count (0 if never extracted)."""
    state = _warmup_state.get(telegram_id)
    return state[1] if state else 0


__all__ = [
    "should_full_extract",
    "reset_warmup",
    "get_warmup_count",
]
