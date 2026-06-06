"""Tests for contact prefetch cache (src/bot/prefetch.py).

Covers:
- Cache hit after prefetch (get_cached_contact returns results)
- Cache miss (cold start — no data prefetched)
- Cache invalidation
- TTL expiry
- Concurrent access safety
- _extract_contact_hint from message entities
- Config disabled (contact_prefetch_enabled=False)
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Must set env BEFORE importing src to avoid Settings validation errors ──
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:TEST_TOKEN_HERE_abcdefghijklmnopqrstuvwx"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"
os.environ["API_ID"] = "12345"
os.environ["API_HASH"] = "0123456789abcdef0123456789abcdef"

from src.bot.prefetch import (
    _CachedEntry,
    _contact_cache,
    _cache_ttl,
    _CACHE_LOCK,
    _refresh_ttl,
    get_cached_contact,
    invalidate_contact,
    invalidate_all,
    prefetch_contact,
    _fuzzy_match,
)
from src.db.models import Contact
from src.core.contacts.contact_resolver import ContactCandidate


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


def _make_contact(
    peer_id: int = 1,
    display_name: str = "Test User",
    username: str | None = None,
    phone: str | None = None,
    user_id: int = 1,
) -> Contact:
    """Create a minimal Contact for testing."""
    c = Contact()
    c.peer_id = peer_id
    c.display_name = display_name
    c.username = username
    c.phone = phone
    c.user_id = user_id
    c.peer_kind = "user"
    c.is_bot = False
    c.is_archived = False
    return c


def _make_cached_entry(
    contacts: list[Contact] | None = None,
    resolved: dict | None = None,
    ts: float | None = None,
) -> _CachedEntry:
    return _CachedEntry(
        contacts=contacts or [],
        resolved=resolved or {},
        ts=ts if ts is not None else time.monotonic(),
    )


def _make_candidate(
    peer_id: int = 1,
    display_name: str = "Test",
    username: str | None = None,
    score: int = 90,
) -> ContactCandidate:
    return ContactCandidate(
        peer_id=peer_id,
        display_name=display_name,
        username=username,
        peer_kind="user",
        score=score,
    )


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_cache():
    """Ensure cache is clean before and after each test."""
    invalidate_all()
    yield
    invalidate_all()


# ══════════════════════════════════════════════════════════════════════
# Tests: _fuzzy_match
# ══════════════════════════════════════════════════════════════════════


def test_fuzzy_match_exact():
    contacts = [_make_contact(1, "John Doe", "johndoe")]
    results = _fuzzy_match("John", contacts)
    assert len(results) == 1
    assert results[0].peer_id == 1
    assert results[0].display_name == "John Doe"


def test_fuzzy_match_username():
    contacts = [
        _make_contact(1, "John Doe", "johndoe"),
        _make_contact(2, "Jane Smith", "janesmith"),
    ]
    results = _fuzzy_match("@johndoe", contacts)
    assert len(results) >= 1
    assert any(r.peer_id == 1 for r in results)


def test_fuzzy_match_phone():
    contacts = [_make_contact(1, "John", phone="+79161234567")]
    results = _fuzzy_match("+79161234567", contacts)
    assert len(results) == 1
    assert results[0].peer_id == 1


def test_fuzzy_match_no_match():
    contacts = [_make_contact(1, "John Doe")]
    results = _fuzzy_match("Zxywq", contacts, min_score=90)
    assert results == []


def test_fuzzy_match_empty_contacts():
    results = _fuzzy_match("test", [])
    assert results == []


def test_fuzzy_match_empty_query():
    contacts = [_make_contact(1, "John")]
    results = _fuzzy_match("", contacts)
    assert results == []


def test_fuzzy_match_limit():
    contacts = [_make_contact(i, f"User {i}", f"user{i}") for i in range(1, 11)]
    results = _fuzzy_match("User", contacts, limit=3)
    assert len(results) == 3


def test_fuzzy_match_min_score_filter():
    contacts = [
        _make_contact(1, "John Doe"),
        _make_contact(2, "Jane Poe"),
    ]
    results = _fuzzy_match("John", contacts, min_score=80)
    assert len(results) == 1
    assert results[0].peer_id == 1


# ══════════════════════════════════════════════════════════════════════
# Tests: get_cached_contact
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cache_hit_after_prefetch():
    """After prefetch, get_cached_contact returns matching results."""
    contacts = [_make_contact(1, "Alice", "alice")]
    async with _CACHE_LOCK:
        _contact_cache[100] = _make_cached_entry(contacts=contacts)

    result = await get_cached_contact(100, "Alice")
    assert result is not None
    assert len(result) == 1
    assert result[0].display_name == "Alice"
    assert result[0].peer_id == 1


@pytest.mark.asyncio
async def test_cache_hit_resolved_entry():
    """Exact resolved entries are checked first."""
    contacts = [_make_contact(1, "Alice")]
    resolved = {"alice": _make_candidate(1, "Alice", score=95)}
    async with _CACHE_LOCK:
        _contact_cache[100] = _make_cached_entry(contacts=contacts, resolved=resolved)

    result = await get_cached_contact(100, "Alice")
    assert result is not None
    assert len(result) == 1
    assert result[0].score == 95


@pytest.mark.asyncio
async def test_cache_miss_cold_start():
    """No data in cache → returns None."""
    result = await get_cached_contact(999, "Nobody")
    assert result is None


@pytest.mark.asyncio
async def test_cache_miss_different_user():
    """User 100 has data, but user 200 queries — should not leak."""
    contacts = [_make_contact(1, "Alice")]
    async with _CACHE_LOCK:
        _contact_cache[100] = _make_cached_entry(contacts=contacts)

    result = await get_cached_contact(200, "Alice")
    assert result is None  # Cache is per-user


@pytest.mark.asyncio
async def test_cache_miss_expired():
    """Expired TTL → returns None."""
    contacts = [_make_contact(1, "Alice")]
    async with _CACHE_LOCK:
        _contact_cache[100] = _CachedEntry(
            contacts=contacts,
            resolved={},
            ts=time.monotonic() - 9999,  # way past TTL
        )

    result = await get_cached_contact(100, "Alice")
    assert result is None


@pytest.mark.asyncio
async def test_cache_empty_contacts():
    """Cached user has no contacts → returns None (after fuzzy match finds nothing)."""
    async with _CACHE_LOCK:
        _contact_cache[100] = _make_cached_entry(contacts=[])

    result = await get_cached_contact(100, "Alice")
    assert result is None


@pytest.mark.asyncio
async def test_get_cached_contact_config_disabled(monkeypatch):
    """When config says disabled, returns None even if cache has data."""
    monkeypatch.setattr("src.bot.prefetch.settings.contact_prefetch_enabled", False)
    contacts = [_make_contact(1, "Alice")]
    async with _CACHE_LOCK:
        _contact_cache[100] = _make_cached_entry(contacts=contacts)

    result = await get_cached_contact(100, "Alice")
    assert result is None


# ══════════════════════════════════════════════════════════════════════
# Tests: prefetch_contact
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_prefetch_contact_populates_cache():
    """prefetch_contact fetches contacts from DB and stores them."""
    with (
        patch("src.bot.prefetch.get_session") as mock_session_ctx,
        patch("src.bot.prefetch.list_contacts") as mock_list,
        patch("src.db.repo.get_or_create_user") as mock_get_user,
    ):
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session

        mock_user = MagicMock()
        mock_user.id = 100
        mock_get_user.return_value = mock_user

        contacts = [_make_contact(1, "Alice"), _make_contact(2, "Bob")]
        mock_list.return_value = contacts

        await prefetch_contact(100)

    async with _CACHE_LOCK:
        entry = _contact_cache.get(100)
    assert entry is not None
    assert len(entry.contacts) == 2
    assert entry.contacts[0].display_name == "Alice"


@pytest.mark.asyncio
async def test_prefetch_contact_with_hint():
    """prefetch_contact with hint resolves via Telethon and caches."""
    with (
        patch("src.bot.prefetch.get_session") as mock_session_ctx,
        patch("src.bot.prefetch.list_contacts") as mock_list,
        patch("src.bot.prefetch.resolve") as mock_resolve,
        patch("src.db.repo.get_or_create_user") as mock_get_user,
    ):
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_user = MagicMock()
        mock_user.id = 100
        mock_get_user.return_value = mock_user
        mock_list.return_value = [_make_contact(1, "Alice")]

        candidate = _make_candidate(1, "Alice", "alice", score=95)
        mock_resolve.return_value = [candidate]

        mock_client = MagicMock()
        mock_owner = MagicMock()

        await prefetch_contact(
            100, contact_hint="alice", telethon_client=mock_client, owner=mock_owner
        )

    async with _CACHE_LOCK:
        entry = _contact_cache.get(100)
    assert entry is not None
    assert "alice" in entry.resolved
    assert entry.resolved["alice"].peer_id == 1


@pytest.mark.asyncio
async def test_prefetch_contact_skips_if_fresh():
    """If user already has fresh cache, skip prefetch."""
    contacts = [_make_contact(1, "Alice")]
    async with _CACHE_LOCK:
        _contact_cache[100] = _make_cached_entry(contacts=contacts)

    with (
        patch("src.bot.prefetch.get_session") as mock_session_ctx,
        patch("src.bot.prefetch.list_contacts") as mock_list,
        patch("src.db.repo.get_or_create_user") as mock_get_user,
    ):
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_user = MagicMock()
        mock_user.id = 100
        mock_get_user.return_value = mock_user

        # This should NOT be called because cache is fresh
        mock_list.return_value = []

        await prefetch_contact(100)

    # list_contacts should NOT have been called
    mock_list.assert_not_called()


@pytest.mark.asyncio
async def test_prefetch_contact_config_disabled(monkeypatch):
    """When config says disabled, prefetch does nothing."""
    monkeypatch.setattr("src.bot.prefetch.settings.contact_prefetch_enabled", False)

    with patch("src.bot.prefetch.get_session") as mock_ctx:
        await prefetch_contact(100)

    mock_ctx.assert_not_called()
    async with _CACHE_LOCK:
        assert _contact_cache.get(100) is None


@pytest.mark.asyncio
async def test_prefetch_contact_handles_db_error():
    """DB errors during prefetch are caught and logged, never raised."""
    with patch("src.bot.prefetch.get_session") as mock_session_ctx:
        mock_session_ctx.return_value.__aenter__.side_effect = RuntimeError("DB down")

        # Should not raise
        await prefetch_contact(100)

    # Cache should remain empty
    async with _CACHE_LOCK:
        assert _contact_cache.get(100) is None


# ══════════════════════════════════════════════════════════════════════
# Tests: invalidation
# ══════════════════════════════════════════════════════════════════════


def test_invalidate_contact():
    """Invalidate removes user's cache entry."""
    contacts = [_make_contact(1, "Alice")]

    async def _setup():
        async with _CACHE_LOCK:
            _contact_cache[100] = _make_cached_entry(contacts=contacts)

    asyncio.run(_setup())
    assert 100 in _contact_cache

    invalidate_contact(100)
    assert 100 not in _contact_cache


def test_invalidate_contact_missing():
    """Invalidating a non-existent user does nothing."""
    invalidate_contact(99999)  # should not raise


def test_invalidate_all():
    """Invalidate all clears entire cache."""

    async def _setup():
        async with _CACHE_LOCK:
            _contact_cache[100] = _make_cached_entry(contacts=[])
            _contact_cache[200] = _make_cached_entry(contacts=[])

    asyncio.run(_setup())
    assert len(_contact_cache) == 2

    invalidate_all()
    assert len(_contact_cache) == 0


# ══════════════════════════════════════════════════════════════════════
# Tests: TTL expiry
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cache_ttl_expiry():
    """After TTL, entry is expired and get_cached_contact returns None."""
    contacts = [_make_contact(1, "Alice")]
    # Set a very short TTL for this test
    old_ttl = None
    try:
        old_ttl = _cache_ttl
        import src.bot.prefetch as prefetch_mod

        prefetch_mod._cache_ttl = 0.01  # 10ms TTL

        async with _CACHE_LOCK:
            _contact_cache[100] = _make_cached_entry(contacts=contacts)

        # Wait for TTL to expire
        await asyncio.sleep(0.02)

        result = await get_cached_contact(100, "Alice")
        assert result is None
    finally:
        if old_ttl is not None:
            import src.bot.prefetch as prefetch_mod

            prefetch_mod._cache_ttl = old_ttl


@pytest.mark.asyncio
async def test_cleanup_stale_removes_expired():
    """get_cached_contact cleanups stale entries before checking."""
    contacts = [_make_contact(1, "Alice")]
    old_ttl = None
    try:
        old_ttl = _cache_ttl
        import src.bot.prefetch as prefetch_mod

        prefetch_mod._cache_ttl = 0.01

        async with _CACHE_LOCK:
            _contact_cache[100] = _CachedEntry(
                contacts=contacts, resolved={}, ts=time.monotonic() - 9999
            )

        result = await get_cached_contact(100, "Alice")
        assert result is None
        # The stale entry should have been cleaned up
        async with _CACHE_LOCK:
            assert 100 not in _contact_cache
    finally:
        if old_ttl is not None:
            import src.bot.prefetch as prefetch_mod

            prefetch_mod._cache_ttl = old_ttl


# ══════════════════════════════════════════════════════════════════════
# Tests: concurrent access
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_concurrent_get_cached_contact():
    """Multiple concurrent reads don't corrupt cache."""
    contacts = [_make_contact(1, "Alice"), _make_contact(2, "Bob")]
    async with _CACHE_LOCK:
        _contact_cache[100] = _make_cached_entry(contacts=contacts)
        _contact_cache[200] = _make_cached_entry(contacts=contacts)

    async def reader(uid: int):
        result = await get_cached_contact(uid, "Alice")
        return result

    tasks = [reader(100) for _ in range(10)] + [reader(200) for _ in range(10)]
    results = await asyncio.gather(*tasks)

    for r in results:
        assert r is not None
        assert len(r) == 1
        assert r[0].display_name == "Alice"


@pytest.mark.asyncio
async def test_concurrent_invalidate_and_read():
    """Invalidate while reading doesn't crash."""
    contacts = [_make_contact(1, "Alice")]
    async with _CACHE_LOCK:
        _contact_cache[100] = _make_cached_entry(contacts=contacts)

    async def reader():
        for _ in range(50):
            await get_cached_contact(100, "Alice")
            await asyncio.sleep(0)

    async def invalidator():
        for _ in range(10):
            invalidate_contact(100)
            await asyncio.sleep(0)

    await asyncio.gather(reader(), invalidator())
    # No exception = success


# ══════════════════════════════════════════════════════════════════════
# Tests: _extract_contact_hint
# ══════════════════════════════════════════════════════════════════════


def test_extract_contact_hint_mention():
    """@mention entity yields username."""
    from src.bot.handlers.free_text import _extract_contact_hint
    from unittest.mock import MagicMock

    msg = MagicMock()
    msg.text = "@alice hello"
    entity = MagicMock()
    entity.type = "mention"
    entity.offset = 0
    entity.length = 6
    msg.entities = [entity]
    msg.reply_to_message = None

    hint = _extract_contact_hint(msg)
    assert hint == "alice"


def test_extract_contact_hint_text_mention():
    """text_mention with user yields username."""
    from src.bot.handlers.free_text import _extract_contact_hint
    from unittest.mock import MagicMock

    msg = MagicMock()
    msg.text = "hello"
    entity = MagicMock()
    entity.type = "text_mention"
    entity.user = MagicMock()
    entity.user.username = "bob"
    entity.user.first_name = "Bob"
    msg.entities = [entity]
    msg.reply_to_message = None

    hint = _extract_contact_hint(msg)
    assert hint == "bob"


def test_extract_contact_hint_text_mention_no_username():
    """text_mention without username falls back to first_name."""
    from src.bot.handlers.free_text import _extract_contact_hint
    from unittest.mock import MagicMock

    msg = MagicMock()
    msg.text = "hello"
    entity = MagicMock()
    entity.type = "text_mention"
    entity.user = MagicMock()
    entity.user.username = None
    entity.user.first_name = "Charlie"
    msg.entities = [entity]
    msg.reply_to_message = None

    hint = _extract_contact_hint(msg)
    assert hint == "Charlie"


def test_extract_contact_hint_reply():
    """Reply context yields replied user's username."""
    from src.bot.handlers.free_text import _extract_contact_hint
    from unittest.mock import MagicMock

    msg = MagicMock()
    msg.entities = None
    reply = MagicMock()
    reply.from_user = MagicMock()
    reply.from_user.username = "dave"
    reply.from_user.first_name = "Dave"
    msg.reply_to_message = reply

    hint = _extract_contact_hint(msg)
    assert hint == "dave"


def test_extract_contact_hint_reply_no_username():
    """Reply without username falls back to first_name."""
    from src.bot.handlers.free_text import _extract_contact_hint
    from unittest.mock import MagicMock

    msg = MagicMock()
    msg.entities = None
    reply = MagicMock()
    reply.from_user = MagicMock()
    reply.from_user.username = None
    reply.from_user.first_name = "Eve"
    msg.reply_to_message = reply

    hint = _extract_contact_hint(msg)
    assert hint == "Eve"


def test_extract_contact_hint_none():
    """No entities and no reply → returns None."""
    from src.bot.handlers.free_text import _extract_contact_hint
    from unittest.mock import MagicMock

    msg = MagicMock()
    msg.entities = None
    msg.reply_to_message = None

    hint = _extract_contact_hint(msg)
    assert hint is None


# ══════════════════════════════════════════════════════════════════════
# Tests: _refresh_ttl
# ══════════════════════════════════════════════════════════════════════


def test_refresh_ttl():
    """_refresh_ttl updates cache TTL from settings."""
    old = _cache_ttl
    try:
        import src.bot.prefetch as prefetch_mod

        prefetch_mod.settings.contact_cache_ttl = 600
        _refresh_ttl()
        assert prefetch_mod._cache_ttl == 600.0
    finally:
        import src.bot.prefetch as prefetch_mod

        prefetch_mod._cache_ttl = old


# ══════════════════════════════════════════════════════════════════════
# Tests: _CachedEntry
# ══════════════════════════════════════════════════════════════════════


def test_cached_entry_slots():
    """_CachedEntry uses __slots__ for memory efficiency."""
    entry = _CachedEntry(contacts=[], resolved={}, ts=0.0)
    assert entry.contacts == []
    assert entry.resolved == {}
    assert entry.ts == 0.0
    # Verify __slots__ prevents arbitrary attr assignment
    with pytest.raises(AttributeError):
        entry.foo = "bar"
