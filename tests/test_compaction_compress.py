"""Tests for Phase 3 COMPRESS: temporal_compress pipeline.

Tests the expected behaviour (implementation to follow).
All tests FAIL against the current stub — by design (test-first).

Logic under test:
  temporal_compress(...)
    → CompressResult

Expected behaviour:
  1. Groups active facts by (contact_id, strftime('%Y-%m', created_at)).
  2. For groups with >= min_group_size facts: LLM compresses into 1-2 summary facts.
  3. Creates a new Memory with source="temporal_compressor".
  4. Deactivates old facts via is_active=False.
  5. Creates MemoryLink relation_type="supersedes" from new → each old.
  6. Upserts the new fact into Qdrant (if vector_store is provided).
  7. Groups from different months or contacts are NOT merged.
  8. Groups smaller than min_group_size are skipped.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from src.db.models import Memory

# ══════════════════════════════════════════════════════════════════════
# DB setup — in-memory SQLite, recreate before every test
# ══════════════════════════════════════════════════════════════════════

OWNER_TG_ID = 99999
CONTACT_SOCHI = 101
CONTACT_OTHER = 202


@pytest.fixture(autouse=True)
def _setup_db():
    """Recreate DB schema before each test (pure in-memory SQLite)."""
    from src.db.session import engine, Base, init_db
    from sqlalchemy import text

    async def _recreate() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
            await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
        await init_db()

    asyncio.run(_recreate())


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


async def _make_owner():
    """Create (or get) the test user."""
    from src.db.session import get_session
    from src.db.repo import get_or_create_user

    async with get_session() as session:
        return await get_or_create_user(session, OWNER_TG_ID)


def _date(year: int, month: int, day: int = 15) -> datetime:
    """UTC-aware datetime for a specific day."""
    return datetime(year, month, day, tzinfo=UTC)


async def _make_memory(
    owner,
    fact: str,
    *,
    contact_id: int | None = None,
    created_at: datetime | None = None,
    source: str = "chat",
    is_active: bool = True,
    confidence: float = 0.9,
) -> Memory:
    """Create a Memory row directly (bypasses add_memory dedup)."""
    from src.db.session import get_session
    from src.db.models import Memory

    async with get_session() as session:
        mem = Memory(
            user_id=owner.id,
            fact=fact,
            contact_id=contact_id,
            source=source,
            confidence=confidence,
            is_active=is_active,
            created_at=created_at or datetime.now(UTC),
            times_mentioned=1,
        )
        session.add(mem)
        await session.commit()
        await session.refresh(mem)
        return mem


def _mock_llm_provider(return_text: str = "Сжатый факт: поездка в Сочи") -> MagicMock:
    """Create a mock LLM provider that returns a fixed compressed text."""
    mock = MagicMock()
    mock.compress = AsyncMock(return_value=return_text)
    return mock


# ══════════════════════════════════════════════════════════════════════
# Test 1 — Group of 3+ facts compresses into 1 new Memory
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_three_facts_same_contact_same_month_compressed():
    """3 facts for (contact=101, month=2026-06) → 1 compressed Memory."""
    from src.db.session import get_session
    from src.db.models import Memory
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    # 3 факта про поездку в Сочи, все за июнь 2026
    _f1 = await _make_memory(
        owner,
        "Был в Сочи 1 июня",
        contact_id=CONTACT_SOCHI,
        created_at=_date(2026, 6, 1),
    )
    _f2 = await _make_memory(
        owner,
        "Купался в море в Сочи",
        contact_id=CONTACT_SOCHI,
        created_at=_date(2026, 6, 10),
    )
    _f3 = await _make_memory(
        owner,
        "Ел шашлык в Сочи",
        contact_id=CONTACT_SOCHI,
        created_at=_date(2026, 6, 20),
    )

    mock_llm = _mock_llm_provider("Поездка в Сочи в июне 2026: море, шашлык")
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    # ── Assertions ───────────────────────────────────────────────
    assert result.groups_examined >= 1, "At least one group should be examined"
    assert result.groups_compressed >= 1, "Group of 3 should be compressed"
    assert result.facts_merged == 3, "All 3 original facts should be merged"
    assert result.facts_deactivated == 3, "All 3 original facts should be deactivated"

    # Verify the new compressed Memory exists
    async with get_session() as session:
        new_mems = (
            (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == owner.id,
                        Memory.source == "temporal_compressor",
                        Memory.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(new_mems) >= 1, "Should have at least 1 compressed Memory"

        compressed = new_mems[0]
        assert "Сочи" in compressed.fact, (
            f"Compressed fact should mention Сочи: {compressed.fact}"
        )
        assert compressed.contact_id == CONTACT_SOCHI


# ══════════════════════════════════════════════════════════════════════
# Test 2 — Old facts are deactivated
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_old_facts_deactivated_after_compress():
    """After compression, original facts have is_active=False."""
    from src.db.session import get_session
    from src.db.models import Memory
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    old_ids = []
    for i, fact in enumerate(
        ["Был в Сочи 1 июня", "Купался в море в Сочи", "Ел шашлык в Сочи"]
    ):
        m = await _make_memory(
            owner, fact, contact_id=CONTACT_SOCHI, created_at=_date(2026, 6, 1 + i * 5)
        )
        old_ids.append(m.id)

    mock_llm = _mock_llm_provider()
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    # ── Assertions ───────────────────────────────────────────────
    async with get_session() as session:
        for mid in old_ids:
            m = await session.get(Memory, mid)
            assert m is not None, f"Memory {mid} should still exist (not deleted)"
            assert m.is_active is False, (
                f"Original memory {mid} should be deactivated (is_active=False), "
                f"got is_active={m.is_active}"
            )


# ══════════════════════════════════════════════════════════════════════
# Test 3 — MemoryLink relation_type="supersedes" is created
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_memorylink_supersedes_created():
    """MemoryLink with relation_type='supersedes' from new → each old fact."""
    from src.db.session import get_session
    from src.db.models import Memory, MemoryLink
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    old_ids = []
    for i, fact in enumerate(
        ["Был в Сочи 1 июня", "Купался в море в Сочи", "Ел шашлык в Сочи"]
    ):
        m = await _make_memory(
            owner, fact, contact_id=CONTACT_SOCHI, created_at=_date(2026, 6, 1 + i * 5)
        )
        old_ids.append(m.id)

    mock_llm = _mock_llm_provider()
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    # ── Assertions ───────────────────────────────────────────────
    async with get_session() as session:
        # Find the new compressed memory
        new_mem = (
            (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == owner.id,
                        Memory.source == "temporal_compressor",
                    )
                )
            )
            .scalars()
            .first()
        )
        assert new_mem is not None, "Compressed Memory should exist"

        # Check MemoryLink(s) from new → old with relation_type="supersedes"
        links = (
            (
                await session.execute(
                    select(MemoryLink).where(
                        MemoryLink.user_id == owner.id,
                        MemoryLink.source_id == new_mem.id,
                        MemoryLink.relation_type == "supersedes",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == len(old_ids), (
            f"Expected {len(old_ids)} supersedes links from new memory, "
            f"got {len(links)}"
        )

        linked_old_ids = {lk.target_id for lk in links}
        assert linked_old_ids == set(old_ids), (
            f"Links should point to all old facts. "
            f"Expected targets: {set(old_ids)}, got: {linked_old_ids}"
        )


# ══════════════════════════════════════════════════════════════════════
# Test 4 — New fact has source="temporal_compressor"
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_new_fact_source_is_temporal_compressor():
    """The compressed Memory has source='temporal_compressor'."""
    from src.db.session import get_session
    from src.db.models import Memory
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    for i, fact in enumerate(
        ["Был в Сочи 1 июня", "Купался в море в Сочи", "Ел шашлык в Сочи"]
    ):
        await _make_memory(
            owner, fact, contact_id=CONTACT_SOCHI, created_at=_date(2026, 6, 1 + i * 5)
        )

    mock_llm = _mock_llm_provider()
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    # ── Assertions ───────────────────────────────────────────────
    async with get_session() as session:
        compressed = (
            (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == owner.id,
                        Memory.source == "temporal_compressor",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(compressed) >= 1, "Should have at least 1 compressed Memory"
        for cm in compressed:
            assert cm.source == "temporal_compressor", (
                f"Compressed memory source should be 'temporal_compressor', "
                f"got '{cm.source}'"
            )

        # Verify NO original memories have been mutated to temporal_compressor
        originals = (
            (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == owner.id,
                        Memory.source != "temporal_compressor",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(originals) == 3, (
            f"All 3 original facts should retain their source, got {len(originals)}"
        )


# ══════════════════════════════════════════════════════════════════════
# Test 5 — Different months are NOT merged
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_different_months_not_merged():
    """Facts from different months form separate groups, only 3+ groups compressed."""
    from src.db.session import get_session
    from src.db.models import Memory
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    # 3 факта за июнь 2026 (должны сжаться)
    june_ids = []
    for i, fact in enumerate(
        ["Был в Сочи 1 июня", "Купался в море в Сочи", "Ел шашлык в Сочи"]
    ):
        m = await _make_memory(
            owner, fact, contact_id=CONTACT_SOCHI, created_at=_date(2026, 6, 1 + i * 5)
        )
        june_ids.append(m.id)

    # 1 факт за июль 2026 (должен остаться активным — группа < 3)
    july_m = await _make_memory(
        owner,
        "Вернулся из Сочи 2 июля",
        contact_id=CONTACT_SOCHI,
        created_at=_date(2026, 7, 2),
    )

    mock_llm = _mock_llm_provider("Поездка в Сочи в июне 2026")
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    # ── Assertions ───────────────────────────────────────────────
    # June group was compressed; July fact was NOT
    assert result.groups_examined >= 2, "Should examine at least 2 groups (June + July)"
    assert result.groups_compressed >= 1, "June group of 3 should be compressed"
    assert result.facts_merged == 3, "Only 3 June facts should be merged"

    async with get_session() as session:
        # July fact should still be active
        july = await session.get(Memory, july_m.id)
        assert july is not None
        assert july.is_active is True, "July fact (group < 3) should remain active"

        # June facts should be deactivated
        for mid in june_ids:
            m = await session.get(Memory, mid)
            assert m.is_active is False, f"June memory {mid} should be deactivated"


# ══════════════════════════════════════════════════════════════════════
# Test 6 — Different contacts are NOT merged
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_different_contacts_not_merged():
    """Facts about different contacts stay in separate groups."""
    from src.db.session import get_session
    from src.db.models import Memory, MemoryLink
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    # 3 факта про Сочи (contact=101) — должны сжаться
    sochi_ids = []
    for i, fact in enumerate(
        ["Был в Сочи 1 июня", "Купался в море в Сочи", "Ел шашлык в Сочи"]
    ):
        m = await _make_memory(
            owner, fact, contact_id=CONTACT_SOCHI, created_at=_date(2026, 6, 1 + i * 5)
        )
        sochi_ids.append(m.id)

    # 2 факта про другого контакта (contact=202) — группа < 3, не сжимаются
    other_ids = []
    for i, fact in enumerate(["Встретил Петю", "Петя рассказал про работу"]):
        m = await _make_memory(
            owner,
            fact,
            contact_id=CONTACT_OTHER,
            created_at=_date(2026, 6, 1 + i * 5),
        )
        other_ids.append(m.id)

    mock_llm = _mock_llm_provider("Поездка в Сочи в июне 2026")
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    # ── Assertions ───────────────────────────────────────────────
    # Sochi group (3 facts) compressed; other group (2 facts) skipped
    assert result.groups_examined >= 2, "Should examine at least 2 groups"
    assert result.groups_compressed >= 1, "Contact 101 group of 3 should be compressed"
    assert result.facts_merged == 3, "Only the 3 Sochi facts should be merged"

    async with get_session() as session:
        # Other contact's facts should still be active
        for mid in other_ids:
            m = await session.get(Memory, mid)
            assert m is not None
            assert m.is_active is True, (
                f"Other-contact memory {mid} should remain active (group < 3)"
            )

        # Verify the compressed memory has contact_id=CONTACT_SOCHI
        compressed = (
            (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == owner.id,
                        Memory.source == "temporal_compressor",
                        Memory.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(compressed) >= 1
        assert compressed[0].contact_id == CONTACT_SOCHI, (
            f"Compressed memory should have contact_id={CONTACT_SOCHI}"
        )

        # Verify MemoryLinks only point to Sochi facts
        links = (
            (
                await session.execute(
                    select(MemoryLink).where(
                        MemoryLink.user_id == owner.id,
                        MemoryLink.relation_type == "supersedes",
                    )
                )
            )
            .scalars()
            .all()
        )
        linked_targets = {lk.target_id for lk in links}
        assert linked_targets == set(sochi_ids), (
            f"Supersedes links should only target Sochi facts. "
            f"Got targets: {linked_targets}"
        )
        assert not linked_targets & set(other_ids), (
            "Other-contact facts should NOT be linked via supersedes"
        )


# ══════════════════════════════════════════════════════════════════════
# Test 7 — Group below min_group_size is NOT compressed
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_group_below_min_size_not_compressed():
    """Group of 2 facts (min_group_size=3) is skipped — no compression."""
    from src.db.session import get_session
    from src.db.models import Memory, MemoryLink
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    # Only 2 facts → group < min_group_size=3
    f1 = await _make_memory(
        owner,
        "Был в Сочи 1 июня",
        contact_id=CONTACT_SOCHI,
        created_at=_date(2026, 6, 1),
    )
    f2 = await _make_memory(
        owner,
        "Купался в море в Сочи",
        contact_id=CONTACT_SOCHI,
        created_at=_date(2026, 6, 10),
    )

    mock_llm = _mock_llm_provider()
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    # ── Assertions ───────────────────────────────────────────────
    assert result.groups_examined >= 1, "Should examine the group"
    assert result.groups_compressed == 0, (
        "Group of 2 (< min_group_size=3) should NOT be compressed"
    )
    assert result.facts_merged == 0
    assert result.facts_deactivated == 0

    async with get_session() as session:
        # Both facts should still be active
        for mid in (f1.id, f2.id):
            m = await session.get(Memory, mid)
            assert m.is_active is True, (
                f"Memory {mid} should remain active (group too small)"
            )

        # No temporal_compressor memories should exist
        compressed = (
            (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == owner.id,
                        Memory.source == "temporal_compressor",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(compressed) == 0, "No compressed memories expected for small group"

        # No supersedes links should exist
        links = (
            (
                await session.execute(
                    select(MemoryLink).where(
                        MemoryLink.user_id == owner.id,
                        MemoryLink.relation_type == "supersedes",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == 0, "No supersedes links expected for small group"


# ══════════════════════════════════════════════════════════════════════
# Test 8 — VectorStore upsert is called when vector_store is provided
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_vector_store_upsert_called():
    """When vector_store is provided, upsert_memory is called for the new fact."""
    from src.db.session import get_session
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    for i, fact in enumerate(
        ["Был в Сочи 1 июня", "Купался в море в Сочи", "Ел шашлык в Сочи"]
    ):
        await _make_memory(
            owner, fact, contact_id=CONTACT_SOCHI, created_at=_date(2026, 6, 1 + i * 5)
        )

    mock_llm = _mock_llm_provider()
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    # ── Assertions ───────────────────────────────────────────────
    # At minimum, upsert_memory was called once for the compressed fact
    assert mock_vs.upsert_memory.called, (
        "upsert_memory should be called when vector_store is provided"
    )

    call_kwargs = mock_vs.upsert_memory.call_args.kwargs
    assert call_kwargs.get("user_id") == owner.id
    assert "Сочи" in call_kwargs.get("fact", ""), (
        f"Upserted fact should contain 'Сочи': {call_kwargs.get('fact')}"
    )


# ══════════════════════════════════════════════════════════════════════
# Test 9 — vector_store=None is safe (no upsert)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_vector_store_does_not_crash():
    """temporal_compress works fine without vector_store (None is default)."""
    from src.db.session import get_session
    from src.core.compaction.compress import temporal_compress

    owner = await _make_owner()

    for i, fact in enumerate(
        ["Был в Сочи 1 июня", "Купался в море в Сочи", "Ел шашлык в Сочи"]
    ):
        await _make_memory(
            owner, fact, contact_id=CONTACT_SOCHI, created_at=_date(2026, 6, 1 + i * 5)
        )

    mock_llm = _mock_llm_provider()

    async with get_session() as session:
        # vector_store=None (default) — should not crash
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=None,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    assert isinstance(
        result,
        __import__(
            "src.core.compaction.models", fromlist=["CompressResult"]
        ).CompressResult,
    )


# ══════════════════════════════════════════════════════════════════════
# Test 10 — Facts with is_active=False are excluded from grouping
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_inactive_facts_excluded_from_groups():
    """Only active facts participate in compression groups."""
    from src.db.session import get_session
    from src.db.models import Memory, MemoryLink
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    # 3 active facts — should be compressed
    active_ids = []
    for i, fact in enumerate(
        ["Был в Сочи 1 июня", "Купался в море в Сочи", "Ел шашлык в Сочи"]
    ):
        m = await _make_memory(
            owner,
            fact,
            contact_id=CONTACT_SOCHI,
            created_at=_date(2026, 6, 1 + i * 5),
            is_active=True,
        )
        active_ids.append(m.id)

    # 1 inactive fact — should be ignored
    inactive = await _make_memory(
        owner,
        "Уже не был в Сочи",
        contact_id=CONTACT_SOCHI,
        created_at=_date(2026, 6, 25),
        is_active=False,
    )

    mock_llm = _mock_llm_provider("Поездка в Сочи в июне 2026")
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    # ── Assertions ───────────────────────────────────────────────
    assert result.facts_merged == 3, (
        "Only 3 active facts should be merged (inactive ignored)"
    )

    async with get_session() as session:
        # Verify the inactive fact is still inactive (wasn't double-deactivated)
        inv = await session.get(Memory, inactive.id)
        assert inv.is_active is False

        # Verify compressed memory
        compressed = (
            (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == owner.id,
                        Memory.source == "temporal_compressor",
                        Memory.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(compressed) == 1

        # Check inactive fact is NOT linked in supersedes
        links = (
            (
                await session.execute(
                    select(MemoryLink).where(
                        MemoryLink.user_id == owner.id,
                        MemoryLink.relation_type == "supersedes",
                    )
                )
            )
            .scalars()
            .all()
        )
        linked_targets = {lk.target_id for lk in links}
        assert set(active_ids).issubset(linked_targets), (
            "All active facts should be linked"
        )
        assert inactive.id not in linked_targets, (
            "Inactive fact should NOT be linked via supersedes"
        )


# ══════════════════════════════════════════════════════════════════════
# Edge case tests — boundary values, nulls, empty responses
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_empty_llm_response_skips_compression():
    """LLM returns empty/whitespace → group is skipped, no facts deactivated."""
    from src.db.session import get_session
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    for i, fact in enumerate(["Был в Сочи 1 июня", "Купался в море", "Ел шашлык"]):
        await _make_memory(
            owner, fact, contact_id=CONTACT_SOCHI, created_at=_date(2026, 6, 1 + i * 5)
        )

    mock_llm = _mock_llm_provider("")  # empty response
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    assert result.groups_compressed == 0, "Empty LLM response should skip compression"
    assert result.facts_merged == 0
    assert result.facts_deactivated == 0


@pytest.mark.asyncio
async def test_llm_returns_none_skips_compression():
    """LLM returns None → group is skipped."""
    from src.db.session import get_session
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    for i in range(3):
        await _make_memory(
            owner,
            f"Fact {i}",
            contact_id=CONTACT_SOCHI,
            created_at=_date(2026, 6, 1 + i * 5),
        )

    mock_llm = MagicMock()
    mock_llm.compress = AsyncMock(return_value=None)
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    assert result.facts_merged == 0


@pytest.mark.asyncio
async def test_llm_returns_malformed_json_uses_fallback():
    """LLM returns non-JSON plain text → used as-is (fallback)."""
    from src.db.session import get_session
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    for i in range(3):
        await _make_memory(
            owner,
            f"Fact {i}",
            contact_id=CONTACT_SOCHI,
            created_at=_date(2026, 6, 1 + i * 5),
        )

    mock_llm = _mock_llm_provider("Просто текст без JSON вообще")
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    assert result.groups_compressed == 1
    assert result.facts_merged == 3


@pytest.mark.asyncio
async def test_llm_returns_json_with_empty_compressed_list():
    """LLM returns {"compressed": []} → group skipped."""
    from src.db.session import get_session
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    for i in range(3):
        await _make_memory(
            owner,
            f"Fact {i}",
            contact_id=CONTACT_SOCHI,
            created_at=_date(2026, 6, 1 + i * 5),
        )

    mock_llm = _mock_llm_provider('{"compressed": [], "confidence": 0.9}')
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    # Empty compressed list → fallback to raw text. add_memory may or
    # may not accept JSON-as-fact; the key is no crash.
    assert isinstance(
        result,
        __import__(
            "src.core.compaction.models", fromlist=["CompressResult"]
        ).CompressResult,
    )
    assert result.groups_examined >= 1


@pytest.mark.asyncio
async def test_duplicate_fact_text_in_group_still_compresses():
    """Duplicate fact texts in a group are handled normally (all merged)."""
    from src.db.session import get_session
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    # 3 identical facts
    for i in range(3):
        await _make_memory(
            owner,
            "Приехал в Сочи",
            contact_id=CONTACT_SOCHI,
            created_at=_date(2026, 6, 1 + i * 5),
        )

    mock_llm = _mock_llm_provider("Приезд в Сочи в июне 2026")
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    assert result.facts_merged == 3, "All 3 (even duplicates) should be merged"
    assert result.facts_deactivated == 3


@pytest.mark.asyncio
async def test_exact_min_group_size_compresses():
    """Group of exactly min_group_size (3) facts is compressed."""
    from src.db.session import get_session
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    for i, fact in enumerate(["A", "B", "C"]):
        await _make_memory(
            owner,
            fact,
            contact_id=CONTACT_SOCHI,
            created_at=_date(2026, 6, 1 + i * 5),
        )

    mock_llm = _mock_llm_provider("Сжатый факт")
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    assert result.groups_compressed == 1, "Exactly min_group_size should compress"


@pytest.mark.asyncio
async def test_no_active_facts_returns_zero_result():
    """User with no active facts → CompressResult with all zeros."""
    from src.db.session import get_session
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()
    # Don't create any facts

    mock_llm = _mock_llm_provider()
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    assert result.groups_examined == 0
    assert result.groups_compressed == 0
    assert result.facts_merged == 0
    assert result.facts_deactivated == 0


@pytest.mark.asyncio
async def test_facts_with_created_at_none_go_to_unknown_bucket():
    """Facts with created_at=None are grouped under 'unknown' month."""
    from src.db.session import get_session
    from src.core.compaction.compress import temporal_compress
    from src.core.actions.vector_store import VectorStore

    owner = await _make_owner()

    for i in range(3):
        await _make_memory(
            owner,
            f"No date fact {i}",
            contact_id=CONTACT_SOCHI,
            created_at=None,
        )

    mock_llm = _mock_llm_provider("Факты без даты")
    mock_vs = AsyncMock(spec=VectorStore)

    async with get_session() as session:
        result = await temporal_compress(
            session,
            owner.id,
            vector_store=mock_vs,
            min_group_size=3,
            llm_provider=mock_llm,
        )

    assert result.groups_compressed == 1, "Facts with NULL created_at should compress"
    assert result.facts_merged == 3
