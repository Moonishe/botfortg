"""Contact prefetch cache — in-process LRU cache of recent contact lookups.

At message handler start, contacts are prefetched optimistically.
Later when contact resolution is needed, the cache is checked first.
Cache misses fall through to normal DB resolution.

Cache is per-user (not cross-user) to prevent data leaks between
different Telegram accounts using the same bot instance.
"""

from __future__ import annotations

import asyncio
import logging
import time

from rapidfuzz import fuzz, process as fuzz_process

from src.config import settings
from src.db.models import Contact
from src.db.repo import list_contacts
from src.db.session import get_session
from src.core.contacts.contact_resolver import ContactCandidate, resolve

logger = logging.getLogger(__name__)

# ── In-process cache ──────────────────────────────────────────────────
#
# Structure: user_id → CachedEntry(contacts, timestamp)
# Lock protects concurrent read/write to the dict.


# NOTE: значение читается динамически, а не на этапе импорта —
# чтобы подхватывать изменения настроек без перезапуска.
def _get_cache_ttl() -> float:
    return float(settings.contact_cache_ttl)


_CACHE_LOCK = asyncio.Lock()

# _contact_cache: user_id → _CachedEntry
_contact_cache: dict[int, _CachedEntry] = {}

# Maximum contacts to cache per user
_MAX_CONTACTS_PER_USER = 100


class _CachedEntry:
    __slots__ = ("contacts", "resolved", "ts")

    def __init__(
        self, contacts: list[Contact], resolved: dict[str, ContactCandidate], ts: float
    ) -> None:
        self.contacts = contacts
        self.resolved = resolved  # query → ContactCandidate (for exact hits)
        self.ts = ts


def _is_expired(entry: _CachedEntry) -> bool:
    return (time.monotonic() - entry.ts) > _get_cache_ttl()


async def _cleanup_stale() -> None:
    """Remove expired entries. Must be called inside _CACHE_LOCK."""
    stale = [uid for uid, entry in _contact_cache.items() if _is_expired(entry)]
    for uid in stale:
        del _contact_cache[uid]


def _fuzzy_match(
    query: str, contacts: list[Contact], min_score: int = 55, limit: int = 5
) -> list[ContactCandidate]:
    """Fuzzy match a query against cached Contact objects.

    Returns list of ContactCandidate sorted by score, best first.
    Uses rapidfuzz WRatio scorer — same strategy as contact_resolver.resolve.
    """
    if not contacts or not query:
        return []

    choices = {c.peer_id: _searchable(c) for c in contacts}
    raw = fuzz_process.extract(
        query,
        choices,
        scorer=fuzz.WRatio,
        limit=limit,
    )

    by_id = {c.peer_id: c for c in contacts}
    results: list[ContactCandidate] = []
    for _, score, peer_id in raw:
        if score < min_score:
            continue
        c = by_id[peer_id]
        results.append(
            ContactCandidate(
                peer_id=c.peer_id,
                display_name=c.display_name,
                username=c.username,
                peer_kind=c.peer_kind,
                score=int(score),
            )
        )
    return results


def _searchable(c: Contact) -> str:
    """Build searchable string for fuzzy matching. Mirrors contact_resolver._searchable."""
    parts = [c.display_name]
    if c.username:
        parts.append("@" + c.username)
    if c.phone:
        parts.append(c.phone)
    return " | ".join(parts)


async def _fetch_contacts(user_id: int) -> list[Contact]:
    """Fetch user's contacts from DB."""
    async with get_session() as session:
        from src.db.repo import get_or_create_user

        user = await get_or_create_user(session, user_id)
        contacts = await list_contacts(
            session, user, kinds=("user",), include_bots=False
        )
        return contacts[:_MAX_CONTACTS_PER_USER]


async def prefetch_contact(
    user_id: int,
    contact_hint: str | None = None,
    *,
    telethon_client=None,
    owner=None,
) -> None:
    """Optimistically prefetch contact data for a user.

    If contact_hint is provided (e.g. from @mention or reply context),
    resolve it immediately using Telethon. Otherwise, prefetch the
    user's contact list from DB.

    Stores results in in-process cache. Never raises — errors are logged.

    Args:
        user_id: The Telegram user ID whose contacts to prefetch.
        contact_hint: Optional name/username to resolve preemptively.
        telethon_client: Telethon client (needed only if contact_hint is given).
        owner: DB User object (needed only if contact_hint is given).

    Returns:
        None — fire-and-forget, stores in cache.
    """
    if not settings.contact_prefetch_enabled:
        return

    try:
        async with _CACHE_LOCK:
            await _cleanup_stale()

            # If there's a fresh cache entry for this user, skip prefetch
            existing = _contact_cache.get(user_id)
            if existing is not None and not _is_expired(existing):
                return

        # Fetch contacts from DB
        contacts = await _fetch_contacts(user_id)

        # Resolve specific hint via Telethon if provided
        resolved: dict[str, ContactCandidate] = {}
        if contact_hint and telethon_client is not None and owner is not None:
            try:
                candidates = await resolve(telethon_client, owner, contact_hint)
                if candidates:
                    resolved[contact_hint.lower()] = candidates[0]
            except Exception:
                logger.debug(
                    "prefetch_contact: hint resolution failed for user=%d hint=%r",
                    user_id,
                    contact_hint,
                    exc_info=True,
                )

        async with _CACHE_LOCK:
            await _cleanup_stale()
            _contact_cache[user_id] = _CachedEntry(
                contacts=contacts,
                resolved=resolved,
                ts=time.monotonic(),
            )
        logger.debug(
            "prefetch_contact: cached %d contacts for user %d (hint=%r)",
            len(contacts),
            user_id,
            contact_hint,
        )
    except Exception:
        logger.debug(
            "prefetch_contact failed for user=%d hint=%r",
            user_id,
            contact_hint,
            exc_info=True,
        )


async def get_cached_contact(
    user_id: int,
    name: str,
    *,
    min_score: int = 55,
    limit: int = 5,
) -> list[ContactCandidate] | None:
    """Try to get contact from prefetch cache.

    Checks for exact hint matches first, then falls back to fuzzy matching
    against all cached contacts.

    Returns:
        list of ContactCandidate on cache hit, None on miss.
    """
    if not settings.contact_prefetch_enabled:
        return None

    async with _CACHE_LOCK:
        await _cleanup_stale()
        entry = _contact_cache.get(user_id)

    if entry is None or _is_expired(entry):
        return None

    # Check exact resolved hits first
    query_lower = name.lower().strip()
    if query_lower in entry.resolved:
        return [entry.resolved[query_lower]]

    # Fuzzy match against cached contacts
    results = _fuzzy_match(name, entry.contacts, min_score=min_score, limit=limit)
    if results:
        return results

    return None


def invalidate_contact(user_id: int) -> None:
    """Invalidate cache for this user (called on contact add/update).

    Synchronous — safe to call from any context without awaiting.
    NOTE: dict.pop атомарен в asyncio (однопоточном event loop) —
    между pop и последующим чтением не может выполниться другой coroutine.
    Гонка бенигна: читатель получит cache miss.
    """
    try:
        _contact_cache.pop(user_id, None)
        logger.debug("invalidated contact cache for user %d", user_id)
    except Exception:
        logger.debug("Non-critical error", exc_info=True)


def invalidate_all() -> None:
    """Clear all cached contact data (e.g. on settings change)."""
    try:
        _contact_cache.clear()
        logger.debug("invalidated all contact caches")
    except Exception:
        logger.debug("Non-critical error", exc_info=True)


# NOTE: TTL читается динамически через _get_cache_ttl(),
# поэтому _refresh_ttl больше не нужна — оставлена для обратной совместимости.
def _refresh_ttl() -> None:
    pass
