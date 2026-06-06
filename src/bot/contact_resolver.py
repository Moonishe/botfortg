"""Bot-level contact resolution with prefetch cache integration.

Wraps the core contact_resolver.resolve() / resolve_with_llm() functions
with a prefetch cache layer. Callers in bot handlers should use
resolve_contact_fast() to benefit from the prefetch cache.

The prefetch cache is populated optimistically at message handler start
(see src.bot.prefetch) and checked here before falling through to
full DB + Telethon resolution.
"""

from __future__ import annotations

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


async def resolve_contact_fast(
    client: "TelegramClient",
    owner: "User",
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
    uid = user_id if user_id is not None else owner.id

    # 1. Try prefetch cache
    cached = await get_cached_contact(uid, query, min_score=min_score, limit=limit)
    if cached is not None:
        if cached:
            logger.debug(
                "resolve_contact_fast: cache hit for user=%d query=%r", uid, query
            )
        return cached

    # 2. Cache miss — full resolution
    logger.debug("resolve_contact_fast: cache miss for user=%d query=%r", uid, query)
    return await resolve(
        client,
        owner,
        query,
        limit=limit,
        min_score=min_score,
        kinds=kinds,
        include_bots=include_bots,
    )


# Re-export for convenience
__all__ = [
    "ContactCandidate",
    "resolve",
    "resolve_with_llm",
    "resolve_contact_fast",
]
