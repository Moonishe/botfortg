"""Tests for Phase 5 GC compaction pipeline — _run_gc.

Tests the _run_gc(session, user_id, vector_store) -> int function
that removes vectors for inactive memories with embedding_hash.

Spec:
    1. SELECT id FROM memories WHERE user_id=? AND is_active=False
       AND embedding_hash IS NOT NULL
    2. Call vector_store.delete_memories(memory_ids)
    3. Return count of deleted vectors
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock

import pytest

# ── Environment setup BEFORE importing src modules ──────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"  # noqa: S105
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.db.session import get_session, init_db
from src.db.models import Memory
from src.db.repo import get_or_create_user

# TDD: import will fail until function is implemented
try:
    from src.core.compaction.orchestrator import _run_gc
except ImportError:
    _run_gc = None  # type: ignore[assignment]

# Import orchestrator module to monkeypatch GC batch size for chunking tests.
import src.core.compaction.orchestrator as _gc_orchestrator

pytestmark = pytest.mark.skipif(
    _run_gc is None,
    reason="_run_gc not yet implemented",
)

TEST_USER_TG_ID = 111
OTHER_USER_TG_ID = 999


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _setup_db():
    """Recreate all tables before each test (in-memory SQLite).

    Uses the same pattern as test_memory_smoke.py and
    test_dreaming_reval.py — drop everything, recreate via init_db().
    """
    from src.db.session import engine, Base
    from sqlalchemy import text

    # Clear the module-level context cache so tests don't see stale user IDs.
    from src.core.context_cache import _cache

    _cache.clear()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
    await init_db()
    yield
    engine.sync_engine.dispose()


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def vector_store():
    """Mock VectorStore with AsyncMock delete_memories."""
    vs = AsyncMock()
    vs.delete_memories = AsyncMock()
    return vs


# ── Helpers ──────────────────────────────────────────────────────────


async def _ensure_user(session, telegram_id: int):
    """Get or create a test user."""
    return await get_or_create_user(session, telegram_id, use_cache=False)


async def _create_memory(
    session,
    user_id: int,
    *,
    is_active: bool = True,
    embedding_hash: str | None = None,
    fact: str = "test fact",
) -> Memory:
    """Create and flush a Memory row with controlled values."""
    mem = Memory(
        user_id=user_id,
        fact=fact,
        is_active=is_active,
        embedding_hash=embedding_hash,
    )
    session.add(mem)
    await session.flush()
    return mem


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inactive_with_hash_calls_delete_memories(vector_store):
    """Inactive facts with embedding_hash → delete_memories is called."""
    async with get_session() as session:
        owner = await _ensure_user(session, TEST_USER_TG_ID)

        m1 = await _create_memory(
            session, owner.id, is_active=False, embedding_hash="abc123"
        )
        m2 = await _create_memory(
            session, owner.id, is_active=False, embedding_hash="def456"
        )
        # Active with hash — should NOT be included
        await _create_memory(session, owner.id, is_active=True, embedding_hash="xyz789")

        count = await _run_gc(session, owner.id, vector_store=vector_store)

        assert count == 2
        vector_store.delete_memories.assert_called_once()
        called_ids = set(vector_store.delete_memories.call_args[0][0])
        assert called_ids == {m1.id, m2.id}


@pytest.mark.asyncio
async def test_gc_clears_embedding_hash_after_delete(vector_store):
    """embedding_hash is set to None after successful GC delete."""
    async with get_session() as session:
        owner = await _ensure_user(session, TEST_USER_TG_ID)
        m = await _create_memory(
            session, owner.id, is_active=False, embedding_hash="abc123"
        )

        count = await _run_gc(session, owner.id, vector_store=vector_store)
        assert count == 1

        refreshed = await session.get(Memory, m.id)
        assert refreshed is not None
        assert refreshed.embedding_hash is None


@pytest.mark.asyncio
async def test_active_facts_not_deleted(vector_store):
    """Active facts are NOT included in delete call."""
    async with get_session() as session:
        owner = await _ensure_user(session, TEST_USER_TG_ID)

        await _create_memory(session, owner.id, is_active=True, embedding_hash="hash1")
        await _create_memory(session, owner.id, is_active=True, embedding_hash="hash2")
        # One inactive to confirm delete is called with correct candidates
        m = await _create_memory(
            session, owner.id, is_active=False, embedding_hash="hash3"
        )

        count = await _run_gc(session, owner.id, vector_store=vector_store)

        assert count == 1
        vector_store.delete_memories.assert_called_once_with([m.id])


@pytest.mark.asyncio
async def test_facts_without_hash_not_deleted(vector_store):
    """Facts without embedding_hash are NOT included in delete call."""
    async with get_session() as session:
        owner = await _ensure_user(session, TEST_USER_TG_ID)

        await _create_memory(session, owner.id, is_active=False, embedding_hash=None)
        m = await _create_memory(
            session, owner.id, is_active=False, embedding_hash="only_hash"
        )

        count = await _run_gc(session, owner.id, vector_store=vector_store)

        assert count == 1
        vector_store.delete_memories.assert_called_once_with([m.id])


@pytest.mark.asyncio
async def test_returns_correct_count(vector_store):
    """Returns correct count of deleted vectors (not active, not null-hash)."""
    async with get_session() as session:
        owner = await _ensure_user(session, TEST_USER_TG_ID)

        for i in range(5):
            await _create_memory(
                session, owner.id, is_active=False, embedding_hash=f"hash{i}"
            )
        # These should NOT be counted:
        await _create_memory(
            session, owner.id, is_active=True, embedding_hash="active_hash"
        )
        await _create_memory(session, owner.id, is_active=False, embedding_hash=None)

        count = await _run_gc(session, owner.id, vector_store=vector_store)
        assert count == 5


@pytest.mark.asyncio
async def test_ignores_other_users(vector_store):
    """Facts belonging to another user are not processed."""
    async with get_session() as session:
        owner = await _ensure_user(session, TEST_USER_TG_ID)
        other = await _ensure_user(session, OTHER_USER_TG_ID)

        await _create_memory(
            session, other.id, is_active=False, embedding_hash="other_hash"
        )
        m = await _create_memory(
            session, owner.id, is_active=False, embedding_hash="owner_hash"
        )

        count = await _run_gc(session, owner.id, vector_store=vector_store)

        assert count == 1
        vector_store.delete_memories.assert_called_once_with([m.id])


@pytest.mark.asyncio
async def test_no_candidates_skips_delete(vector_store):
    """No matching candidates → delete_memories NOT called, returns 0."""
    async with get_session() as session:
        owner = await _ensure_user(session, TEST_USER_TG_ID)

        await _create_memory(session, owner.id, is_active=True, embedding_hash="hash1")
        await _create_memory(session, owner.id, is_active=False, embedding_hash=None)

        count = await _run_gc(session, owner.id, vector_store=vector_store)

        assert count == 0
        vector_store.delete_memories.assert_not_called()


@pytest.mark.asyncio
async def test_chunked_gc_batches(vector_store, monkeypatch):
    """GC respects _run_gc_batch_size and deletes in chunks."""
    assert _run_gc is not None
    import types

    fake_settings = types.SimpleNamespace(compaction_gc_batch_size=2)
    monkeypatch.setattr(_gc_orchestrator, "settings", fake_settings)

    async with get_session() as session:
        owner = await _ensure_user(session, TEST_USER_TG_ID)

        created = [
            await _create_memory(
                session, owner.id, is_active=False, embedding_hash=f"h{i}"
            )
            for i in range(5)
        ]
        ids = [m.id for m in created]

        count = await _run_gc(session, owner.id, vector_store=vector_store)

        assert count == 5
        assert vector_store.delete_memories.call_count == 3
        called_batches = [
            c.args[0] for c in vector_store.delete_memories.call_args_list
        ]
        assert len(called_batches[0]) == 2
        assert len(called_batches[1]) == 2
        assert len(called_batches[2]) == 1
        assert set().union(*called_batches) == set(ids)


@pytest.mark.asyncio
async def test_gc_skips_failed_batch_and_continues(vector_store, monkeypatch):
    """If a batch delete fails, GC skips it and continues with later batches."""
    assert _run_gc is not None
    import types

    fake_settings = types.SimpleNamespace(compaction_gc_batch_size=2)
    monkeypatch.setattr(_gc_orchestrator, "settings", fake_settings)

    call_count = 0

    async def _failing_delete(ids):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise RuntimeError("boom")
        return None

    vector_store.delete_memories = AsyncMock(side_effect=_failing_delete)

    async with get_session() as session:
        owner = await _ensure_user(session, TEST_USER_TG_ID)

        for i in range(5):
            await _create_memory(
                session, owner.id, is_active=False, embedding_hash=f"h{i}"
            )

        count = await _run_gc(session, owner.id, vector_store=vector_store)

        # Only the first batch of 2 ids succeeded; subsequent batches failed.
        assert count == 2
        assert vector_store.delete_memories.call_count == 3
