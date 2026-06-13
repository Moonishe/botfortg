"""Persistent rules system — user-defined rules remembered across sessions.

Rules are stored as Memory facts with memory_type="rule" and pinned=True.
They are injected into the LLM system prompt via the confirmed_rules pipeline.

Usage:
    await add_rule(telegram_id, "никогда не используй смайлики")
    rules = await get_rules(telegram_id)  # → ["никогда не используй смайлики"]
    await delete_rule(telegram_id, "никогда не используй смайлики")
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from src.db.models._memory import Memory
from src.db.repo import get_or_create_user
from src.db.repos.memory_repo import add_memory as _add_memory
from src.db.session import get_session

logger = logging.getLogger(__name__)

_MEMORY_TYPE_RULE = "rule"


async def add_rule(telegram_id: int, rule_text: str) -> bool:
    """Add a persistent rule for the user. Returns True if added.

    Deduplication: identical rules will increment times_mentioned
    rather than creating duplicates (handled by add_memory).
    """
    rule_text = rule_text.strip()
    if len(rule_text) < 3:
        logger.debug("Rule too short: %r", rule_text)
        return False

    async with get_session() as session:
        user = await get_or_create_user(session, telegram_id)
        if user is None:
            logger.warning("User not found for telegram_id=%d", telegram_id)
            return False

        await _add_memory(
            session,
            user,
            fact=rule_text,
            source="user",
            memory_type=_MEMORY_TYPE_RULE,
            pinned=True,
            confidence=1.0,
            deduplicate=True,
        )
        await session.commit()

    _schedule_cache_invalidation(telegram_id)
    logger.info("Rule added for user %d: %s", telegram_id, rule_text[:80])
    return True


async def get_rules(telegram_id: int) -> list[str]:
    """Get all active persistent rules for the user."""
    from src.core.context_cache import get as cache_get, put as cache_put

    cache_key = f"persistent_rules:{telegram_id}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    async with get_session() as session:
        user = await get_or_create_user(session, telegram_id)
        if user is None:
            return []

        result = await session.execute(
            select(Memory.fact)
            .where(
                Memory.user_id == user.id,
                Memory.memory_type == _MEMORY_TYPE_RULE,
                Memory.is_active.is_(True),
            )
            .order_by(Memory.created_at.asc())
        )
        rules = [row[0] for row in result.fetchall()]

    await cache_put(cache_key, rules, ttl=30)
    return rules


async def delete_rule(telegram_id: int, rule_text: str) -> bool:
    """Delete (deactivate) a persistent rule. Returns True if found and deleted."""
    rule_text = rule_text.strip()

    async with get_session() as session:
        user = await get_or_create_user(session, telegram_id)
        if user is None:
            return False

        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == user.id,
                Memory.memory_type == _MEMORY_TYPE_RULE,
                Memory.fact == rule_text,
                Memory.is_active.is_(True),
            )
            .limit(1)
        )
        memory = result.scalar_one_or_none()
        if memory is None:
            return False

        memory.is_active = False
        await session.commit()

    _schedule_cache_invalidation(telegram_id)
    logger.info("Rule deleted for user %d: %s", telegram_id, rule_text[:80])
    return True


def _schedule_cache_invalidation(telegram_id: int) -> None:
    """Schedule async cache invalidation (safe to call from sync context)."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_invalidate_cache(telegram_id))
    except RuntimeError:
        pass  # No running loop — cache TTL handles expiry


async def _invalidate_cache(telegram_id: int) -> None:
    from src.core.context_cache import invalidate

    await invalidate(f"persistent_rules:{telegram_id}")
