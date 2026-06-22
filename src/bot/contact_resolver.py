"""Bot-level contact resolution with prefetch cache integration.

Wraps the core contact_resolver.resolve() / resolve_with_llm() functions
with a prefetch cache layer. Callers in bot handlers should use
resolve_contact_fast() to benefit from the prefetch cache.

The prefetch cache is populated optimistically at message handler start
(see src.bot.prefetch) and checked here before falling through to
full DB + Telethon resolution.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from src.core.contacts.contact_resolver import (
    ContactCandidate,
    resolve,
    resolve_with_llm,
)
from src.bot.prefetch import get_cached_contact

if TYPE_CHECKING:
    from telethon import TelegramClient
    from src.db.models import User

logger = logging.getLogger(__name__)

# Deduplication: prevents concurrent resolve() calls for the same query key
_inflight_resolves: dict[str, asyncio.Future] = {}


async def resolve_contact_fast(
    client: TelegramClient,
    owner: User,
    query: str,
    *,
    user_id: int | None = None,
    limit: int = 5,
    min_score: int = 55,
    kinds: tuple[str, ...] = ("user",),
    include_bots: bool = False,
) -> list[ContactCandidate]:
    """Fast contact resolution: check prefetch cache first, then fall back to DB.

    Order of resolution:
    1. Check in-process prefetch cache (src.bot.prefetch)
    2. If cache miss → full DB + Telethon resolution via resolve()

    The cache result already went through fuzzy matching, so we return it directly.

    Args:
        client: Telethon client.
        owner: DB User object.
        query: Name, username, or phone to search.
        user_id: Telegram user ID (for cache lookup). Defaults to owner.id.
        limit: Max candidates to return.
        min_score: Minimum fuzzy match score.
        kinds: Peer kinds to search (default: users only).
        include_bots: Whether to include bot peers.

    Returns:
        List of ContactCandidate matching the query (empty if no match).
    """
    # Guard: empty/whitespace-only query → no candidates possible
    if not query or not query.strip():
        return []

    uid = user_id if user_id is not None else owner.telegram_id

    # 1. Try prefetch cache
    cached = await get_cached_contact(uid, query, min_score=min_score, limit=limit)
    if cached is not None:
        if cached:
            logger.debug(
                "resolve_contact_fast: cache hit for user=%d query=%r", uid, query
            )
        return cached

    # 2. Cache miss — full resolution (deduplicated per query key)
    logger.debug("resolve_contact_fast: cache miss for user=%d query=%r", uid, query)
    query_key = f"{uid}:{query.lower().strip()}"
    if query_key in _inflight_resolves:
        return await _inflight_resolves[query_key]
    future: asyncio.Future[list[ContactCandidate]] = (
        asyncio.get_event_loop().create_future()
    )
    _inflight_resolves[query_key] = future
    try:
        result = await resolve(
            client,
            owner,
            query,
            limit=limit,
            min_score=min_score,
            kinds=kinds,
            include_bots=include_bots,
        )
        future.set_result(result)
        return result
    except Exception as e:
        future.set_exception(e)
        raise
    finally:
        _inflight_resolves.pop(query_key, None)


# Re-export for convenience
__all__ = [
    "ContactCandidate",
    "resolve",
    "resolve_contact_fast",
    "resolve_with_llm",
]
