"""DAG dispatch and dedup utilities — extracted from free_text/_core.py.

Uses lazy imports for _dispatch to avoid circular dependency with _core.py.
"""

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram.fsm.context import FSMContext
    from aiogram.types import Message
    from src.userbot.manager import UserbotManager

from src.bot.handlers.free_text._shared import (
    _DEDUP_CACHE_MAX,
    _DEDUP_CACHE_TTL,
)

logger = logging.getLogger(__name__)

# ── Dedup cache: prevents repeated LLM extraction for same (user, text) ──
_dedup_cache: dict[tuple[int, str], float] = {}  # (owner_id, hash) → timestamp
_dedup_cache_lock = asyncio.Lock()


async def _should_skip_auto_save(owner_id: int, text: str) -> bool:
    """Check if we've already extracted facts from this text recently.

    Uses SHA-256 hash of the first 500 characters of text as a content
    fingerprint. Within the TTL window (60s), identical content from
    the same user is skipped to save LLM tokens.

    Protected by _dedup_cache_lock to prevent race conditions
    from concurrent fire-and-forget tasks.
    """
    now = time.monotonic()
    key = (owner_id, hashlib.sha256(text[:500].encode()).hexdigest())
    async with _dedup_cache_lock:
        if key in _dedup_cache and now - _dedup_cache[key] < _DEDUP_CACHE_TTL:
            return True
        # Evict old entries if cache too big
        if len(_dedup_cache) >= _DEDUP_CACHE_MAX:
            stale = [
                k for k, ts in _dedup_cache.items() if now - ts > _DEDUP_CACHE_TTL * 2
            ]
            if stale:
                for k in stale[:50]:
                    _dedup_cache.pop(k, None)
            else:
                # All entries are fresh — forced eviction of oldest 25%
                force_evict = max(_DEDUP_CACHE_MAX // 4, 1)
                oldest = sorted(_dedup_cache.items(), key=lambda x: x[1])[:force_evict]
                for k, _ in oldest:
                    _dedup_cache.pop(k, None)
                logger.debug(
                    "Dedup cache forced eviction: removed %d fresh entries "
                    "(all %d were within TTL)",
                    len(oldest),
                    _DEDUP_CACHE_MAX,
                )
        _dedup_cache[key] = now
    return False


# ── DAG dispatch ─────────────────────────────────────────────────────────


async def _run_dag_level(
    tasks: list,
    sub_intents: list[dict],
    indices: list[int] | None = None,
) -> None:
    """Запускает группу sub-intents параллельно, логирует ошибки."""
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        logger.warning("DAG level cancelled")
        raise
    for idx, result in enumerate(results):
        if isinstance(result, Exception):
            i = indices[idx] if indices else idx
            logger.error(
                "Sub-intent %d (%s) failed: %s",
                i,
                sub_intents[i].get("intent", "?"),
                result,
            )


async def _dag_dispatch(
    sub_intents: list[dict],
    message: "Message",
    state: "FSMContext",
    userbot_manager: "UserbotManager",
    *,
    tz_name: str,
) -> None:
    """DAG-диспетчер: независимые sub-intents выполняются параллельно.

    Формат sub_intent:
      {"intent": "...", ..., "depends_on": [0, 2]}
      depends_on — список индексов в sub_intents, которые должны выполниться ДО этого.
      Если depends_on отсутствует или пуст — действие считается независимым.

    При циклических зависимостях — fallback на последовательное выполнение.
    """
    # Lazy import to avoid circular dependency with _core.py
    from src.bot.handlers.free_text._core import _dispatch

    if not isinstance(sub_intents, list):
        logger.error(
            "_dag_dispatch: sub_intents is not a list: %r (type=%s)",
            sub_intents,
            type(sub_intents).__name__,
        )
        await message.answer("⚠️ Internal routing error (malformed sub-intents).")
        return
    if not sub_intents:
        await message.answer("Не понял, что сделать.")
        return

    # Guard: validate all sub-intents are dicts
    for i, sub in enumerate(sub_intents):
        if not isinstance(sub, dict):
            logger.error(
                "_dag_dispatch: sub_intents[%d] is not a dict: %r (type=%s)",
                i,
                sub,
                type(sub).__name__,
            )
            await message.answer("⚠️ Internal routing error (malformed sub-intent).")
            return

    n = len(sub_intents)
    if n == 1:
        await _dispatch(
            sub_intents[0], message, state, userbot_manager, tz_name=tz_name
        )
        return

    # Build dependency graph (Kahn's algorithm)
    in_degree = [0] * n
    children: list[list[int]] = [[] for _ in range(n)]
    has_any_dep = False

    for i, sub in enumerate(sub_intents):
        deps = sub.get("depends_on") or []
        if deps:
            has_any_dep = True
        for d in deps:
            if isinstance(d, int) and 0 <= d < n and d != i:
                children[d].append(i)
                in_degree[i] += 1

    # Если ни у одного sub-intent нет depends_on — все независимы → параллельно
    if not has_any_dep:
        tasks = [
            _dispatch(sub, message, state, userbot_manager, tz_name=tz_name)
            for sub in sub_intents
        ]
        await _run_dag_level(tasks, sub_intents)
        return

    # Topo-sort by levels
    level: list[int] = [i for i in range(n) if in_degree[i] == 0]
    levels: list[list[int]] = []
    visited = 0

    while level:
        levels.append(level)
        visited += len(level)
        next_level: list[int] = []
        for node in level:
            for child in children[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_level.append(child)
        level = next_level

    if visited < n:
        # Cycle detected — fallback to sequential
        logger.warning(
            "DAG cycle detected in multi-intent (%d/%d visited), "
            "falling back to sequential",
            visited,
            n,
        )
        for sub in sub_intents:
            try:
                await _dispatch(sub, message, state, userbot_manager, tz_name=tz_name)
            except (
                Exception
            ):  # NOTE: _dispatch может поднять SQLAlchemyError, TelegramAPIError,
                # RequestError, HTTPStatusError — все они безопасно логируются здесь.
                logger.exception(
                    "DAG fallback: sub-intent %s failed", sub.get("intent", "?")
                )
        return

    # Execute per level in parallel
    for level_indices in levels:
        tasks = [
            _dispatch(sub_intents[i], message, state, userbot_manager, tz_name=tz_name)
            for i in level_indices
        ]
        await _run_dag_level(tasks, sub_intents, level_indices)
