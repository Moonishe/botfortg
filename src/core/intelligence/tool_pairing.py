"""Tool co-occurrence tracking with TTL expiry — per-user isolation.

Records which tools are called in sequence. When tool B follows tool A,
the pair (A→B) is tracked with a timestamp. Old pairings expire after TTL.

Usage::

    from src.core.intelligence.tool_pairing import record_tool_call, get_frequent_pairs

    await record_tool_call("search_web", user_id=123)   # call 1
    await record_tool_call("summarize", user_id=123)    # call 2 → records pair

    next_tools = await get_frequent_pairs(
        "search_web", user_id=123
    )  # ["summarize", ...]
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

_TTL_SEC = 3600  # 1 hour
_MAX_PER_TOOL = 50  # max pair entries per tool

# Per-user data: {user_id: {_pairs: defaultdict, _last_tool: str|None}}
_user_pairs: dict[int, dict] = {}
_lock = asyncio.Lock()
# Cache for get_frequent_pairs: {(user_id, tool, min_count): (result, timestamp)}
_cache: dict[tuple[int, str, int], tuple[list[str], float]] = {}
_CACHE_TTL_SEC = 5  # cache results for 5 seconds
_MAX_CACHE_ENTRIES = 200  # ponytail: evict oldest 25% when full, never clear-all
_EVICT_FRACTION = 0.25  # evict oldest 25% entries when cache is full
# User data TTL — evict users inactive > 24 hours
_USER_TTL_SEC = 86400
_user_last_access: dict[int, float] = {}


def _get_user_data(user_id: int) -> dict:
    """Get or create per-user pairing data (sync — no I/O)."""
    if user_id not in _user_pairs:
        _user_pairs[user_id] = {
            "pairs": defaultdict(lambda: deque(maxlen=_MAX_PER_TOOL)),
            "last_tool": None,
        }
    _user_last_access[user_id] = time.monotonic()
    return _user_pairs[user_id]


async def record_tool_call(tool_name: str, *, user_id: int = 0) -> None:
    """Record a tool call in sequence. Pairs with the previous tool for this user.

    Args:
        tool_name: The tool that was just called.
        user_id: User ID for per-user isolation. Default 0 for single-user.
    """
    async with _lock:
        data = _get_user_data(user_id)
        last = data["last_tool"]
        if last is not None and last != tool_name:
            now = time.monotonic()
            data["pairs"][last].append((tool_name, now))
        data["last_tool"] = tool_name
        # Invalidate ALL cache entries for the CHANGED tool
        # (last → tool_name pair added). Must invalidate both 'last' (new pair
        # added to its list) and 'tool_name' (consistency).
        stale_keys = [
            k for k in _cache if k[0] == user_id and k[1] in (last, tool_name)
        ]
        for k in stale_keys:
            _cache.pop(k, None)


async def get_frequent_pairs(
    tool: str, min_count: int = 2, *, user_id: int = 0
) -> list[str]:
    """Return tools frequently called after *tool* (within TTL) for this user.

    Args:
        tool: The reference tool name.
        min_count: Minimum co-occurrence count to be included.
        user_id: User ID for per-user isolation. Default 0 for single-user.
    """
    cache_key = (user_id, tool, min_count)
    now = time.monotonic()

    # Check cache
    cached = _cache.get(cache_key)
    if cached and now - cached[1] < _CACHE_TTL_SEC:
        return cached[0]

    async with _lock:
        # Evict users inactive > _USER_TTL_SEC (memory leak prevention)
        stale_users = [
            uid
            for uid, last in _user_last_access.items()
            if uid in _user_pairs and now - last > _USER_TTL_SEC
        ]
        for uid in stale_users:
            _user_pairs.pop(uid, None)
            _user_last_access.pop(uid, None)
        # Evict oldest cache entries if cache grows too large (instead of clearing ALL)
        if len(_cache) > _MAX_CACHE_ENTRIES:
            evict_count = int(_MAX_CACHE_ENTRIES * _EVICT_FRACTION)
            # Sort by timestamp (second element of value tuple), evict oldest
            stale_keys = sorted(
                _cache.keys(),
                key=lambda k: _cache[k][1],  # timestamp
            )[:evict_count]
            for k in stale_keys:
                _cache.pop(k, None)
        data = _user_pairs.get(user_id)
        if not data:
            result: list[str] = []
            _cache[cache_key] = (result, now)
            return result
        entries = data["pairs"].get(tool)
        if not entries:
            result = []
            _cache[cache_key] = (result, now)
            return result
        counts: dict[str, int] = defaultdict(int)
        for other, ts in entries:
            if now - ts < _TTL_SEC:
                counts[other] += 1
        result = sorted(
            (t for t, c in counts.items() if c >= min_count),
            key=lambda t: counts[t],
            reverse=True,
        )
        _cache[cache_key] = (result, now)
        return result


async def reset(*, user_id: int | None = None) -> None:
    """Clear pairing data (for tests). If user_id given, clears only that user."""
    async with _lock:
        if user_id is not None:
            _user_pairs.pop(user_id, None)
            _user_last_access.pop(user_id, None)
        else:
            _user_pairs.clear()
            _user_last_access.clear()
        _cache.clear()
