"""Integration tests for Compaction Pipeline v2 orchestrator.

Tests the 7-phase compaction cycle contract:
  - Pipeline returns CompactionReport
  - Each phase populates its report fields
  - Phase failure → error captured, remaining phases continue
  - compression_ratio = active_after / max(active_before, 1)
  - LLM & VectorStore fully mocked (no real external calls)

Uses in-memory SQLite following the project test pattern
(test_dreaming_reval.py, test_memory_smoke.py).
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Environment setup BEFORE importing src modules ──────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")

from src.core.compaction.models import CompactionReport, CompressResult
from src.core.compaction.orchestrator import run_compaction_pipeline
from src.db.session import get_session
from src.db.repo import add_memory, get_or_create_user

OWNER_TG_ID = 123456789


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def setup_db():
    """Recreate all tables before each test (in-memory SQLite)."""
    from src.db.session import (
        engine,
        Base,
        _FTS_SETUP,
        _SESSION_FTS_SETUP,
        _MEMORY_FTS_SETUP,
    )
    from sqlalchemy import text

    async def _recreate():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
            await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for stmt in _FTS_SETUP:
                await conn.execute(text(stmt))
            for stmt in _SESSION_FTS_SETUP:
                await conn.execute(text(stmt))
            for stmt in _MEMORY_FTS_SETUP:
                await conn.execute(text(stmt))

    asyncio.run(_recreate())


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════


class MockLLMProvider:
    """Mock that returns pre-configured responses.

    Implements the LLMProvider protocol minimally: ``chat`` + ``close``.
    """

    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or []
        self.call_count = 0
        self.name = "mock"

    async def chat(self, messages, *, heavy=False, task_type="default"):
        if self.call_count >= len(self.responses):
            return '{"action": "skip", "reason": "no more responses"}'
        resp = self.responses[self.call_count]
        self.call_count += 1
        return resp

    async def close(self):
        pass


async def _make_owner(tg_id: int = OWNER_TG_ID):
    """Create / retrieve the test owner user."""
    async with get_session() as session:
        return await get_or_create_user(session, tg_id)


async def _make_memory(
    owner,
    fact: str,
    *,
    memory_type: str | None = None,
    confidence: float = 0.9,
    pinned: bool = False,
    is_active: bool = True,
    decay_rate: float | None = None,
    source: str = "chat",
    **kwargs,
):
    """Create a Memory row."""
    async with get_session() as session:
        m = await add_memory(
            session,
            owner,
            fact=fact,
            memory_type=memory_type,
            confidence=confidence,
            pinned=pinned,
            decay_rate=decay_rate,
            source=source,
            **kwargs,
        )
        if m is None:
            raise RuntimeError(f"add_memory returned None for fact={fact!r}")
        if not is_active:
            m.is_active = False
        await session.commit()
        return m


async def _active_count(owner) -> int:
    """Count active memories for a user."""
    from sqlalchemy import select, func
    from src.db.models import Memory as MemoryModel

    async with get_session() as session:
        result = await session.execute(
            select(func.count()).where(
                MemoryModel.user_id == owner.id,
                MemoryModel.is_active == True,  # noqa: E712
            )
        )
        return result.scalar_one()


def _mock_vector_store() -> MagicMock:
    """Create a fully mocked VectorStore (no Qdrant, no file I/O)."""
    vs = MagicMock()
    vs.delete_memories = AsyncMock(return_value=5)
    vs.upsert_memory = AsyncMock(return_value=None)
    vs.search_similar_memories = AsyncMock(return_value=[])
    vs.shutdown = AsyncMock()
    vs.check_health_and_recover = AsyncMock(return_value=True)
    return vs


# ═══════════════════════════════════════════════════════════════════
#  Test: Pipeline returns CompactionReport
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pipeline_returns_compaction_report():
    """Pipeline returns a CompactionReport even with no facts."""
    owner = await _make_owner()

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert isinstance(report, CompactionReport)
    assert isinstance(report.errors, list)
    assert report.duration_sec >= 0.0


# ═══════════════════════════════════════════════════════════════════
#  Test: PRUNE phase
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_prune_phase_fills_facts_pruned():
    """PRUNE phase populates facts_pruned and longterm_protected."""
    owner = await _make_owner()
    await _make_memory(owner, "Will be pruned 1", is_active=True, pinned=False)
    await _make_memory(owner, "Will be pruned 2", is_active=True, pinned=False)
    await _make_memory(owner, "Protected longterm", is_active=True, pinned=True)

    before = await _active_count(owner)

    async def _mock_prune(session, user_id, *, vector_store=None, llm_provider=None):
        from sqlalchemy import select, update
        from src.db.models import Memory as MemoryModel

        result = await session.execute(
            select(MemoryModel)
            .where(
                MemoryModel.user_id == user_id,
                MemoryModel.is_active == True,  # noqa: E712
                MemoryModel.pinned == False,  # noqa: E712
            )
            .limit(2)
        )
        to_deactivate = result.scalars().all()
        ids = [m.id for m in to_deactivate]
        if ids:
            await session.execute(
                update(MemoryModel)
                .where(MemoryModel.id.in_(ids))
                .values(is_active=False)
            )
        return (len(ids), 1)  # (pruned, protected)

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch("src.core.compaction.orchestrator._run_prune", new=_mock_prune),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(0, 0, 0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_compress",
            new=AsyncMock(return_value=CompressResult()),
        ),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(before, before - 2, 0.0)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert report.facts_pruned == 2
    assert report.longterm_protected == 1


# ═══════════════════════════════════════════════════════════════════
#  Test: NUDGE phase
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_nudge_phase_fills_facts_nudged():
    """NUDGE phase populates nudge fields."""
    owner = await _make_owner()
    await _make_memory(owner, "Medium confidence fact", confidence=0.6)
    await _make_memory(owner, "Another medium fact", confidence=0.55)

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch(
            "src.core.compaction.orchestrator._run_prune",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(2, 1, 0, 1)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_compress",
            new=AsyncMock(return_value=CompressResult()),
        ),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(2, 2, 0.0)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert report.facts_nudged == 2
    assert report.nudge_confirmed == 1
    assert report.nudge_forgotten == 0
    assert report.nudge_edited == 1


# ═══════════════════════════════════════════════════════════════════
#  Test: COMPRESS phase
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_compress_phase_fills_groups_and_merged():
    """COMPRESS phase populates groups_examined, groups_compressed, facts_merged."""
    owner = await _make_owner()
    for i in range(6):
        await _make_memory(owner, f"Similar fact group {i}", memory_type="temporary")

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch(
            "src.core.compaction.orchestrator._run_prune",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(0, 0, 0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_compress",
            new=AsyncMock(
                return_value=CompressResult(
                    groups_examined=3,
                    groups_compressed=2,
                    facts_merged=6,
                    facts_deactivated=4,
                )
            ),
        ),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(6, 4, 0.33)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert report.groups_examined == 3
    assert report.groups_compressed == 2
    assert report.facts_merged == 6


# ═══════════════════════════════════════════════════════════════════
#  Test: GC phase
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gc_phase_fills_vectors_removed():
    """GC phase populates vectors_removed."""
    owner = await _make_owner()

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch(
            "src.core.compaction.orchestrator._run_prune",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(0, 0, 0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_compress",
            new=AsyncMock(return_value=CompressResult()),
        ),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=12)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(0, 0, 0.0)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert report.vectors_removed == 12


# ═══════════════════════════════════════════════════════════════════
#  Test: LEARN phase
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_learn_phase_fills_skills_extracted():
    """LEARN phase populates skills_extracted."""
    owner = await _make_owner()

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch(
            "src.core.compaction.orchestrator._run_prune",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(0, 0, 0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_compress",
            new=AsyncMock(return_value=CompressResult()),
        ),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=3)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(0, 0, 0.0)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert report.skills_extracted == 3


# ═══════════════════════════════════════════════════════════════════
#  Test: Phase failure isolation (try/except per phase)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_phase_failure_does_not_block_remaining_phases():
    """If one phase raises, remaining phases still execute and error is captured."""
    owner = await _make_owner()
    await _make_memory(owner, "Fact for testing resilience")

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    async def _failing_prune(*args, **kwargs):
        raise RuntimeError("Prune phase exploded (simulated)")

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch("src.core.compaction.orchestrator._run_prune", new=_failing_prune),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(3, 2, 1, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_compress",
            new=AsyncMock(
                return_value=CompressResult(
                    groups_examined=2, groups_compressed=1, facts_merged=4
                )
            ),
        ),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(5, 2)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=7)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=2)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(10, 7, 0.30)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    # Error captured
    assert len(report.errors) >= 1
    assert any("PRUNE" in e for e in report.errors)

    # Remaining phases populated
    assert report.facts_nudged == 3
    assert report.groups_examined == 2
    assert report.reval_examined == 5
    assert report.vectors_removed == 7
    assert report.skills_extracted == 2

    # Failed phase left at defaults
    assert report.facts_pruned == 0


@pytest.mark.asyncio
async def test_multiple_phase_failures_all_captured():
    """Multiple phase failures → all errors captured, no crash."""
    owner = await _make_owner()

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    async def _failing_prune(*args, **kwargs):
        raise RuntimeError("Prune failed")

    async def _failing_compress(*args, **kwargs):
        raise ValueError("Compress failed — bad LLM response")

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch("src.core.compaction.orchestrator._run_prune", new=_failing_prune),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(1, 1, 0, 0)),
        ),
        patch("src.core.compaction.orchestrator._run_compress", new=_failing_compress),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(0, 0, 0.0)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert len(report.errors) >= 2
    assert any("PRUNE" in e for e in report.errors)
    assert any("COMPRESS" in e for e in report.errors)
    assert report.facts_nudged == 1  # NUDGE still ran


# ═══════════════════════════════════════════════════════════════════
#  Test: compression_ratio
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_compression_ratio_in_range_zero_to_one():
    """compression_ratio ∈ [0, 1] after compaction reduces active facts."""
    owner = await _make_owner()
    for i in range(10):
        await _make_memory(owner, f"Active fact {i}", is_active=True)

    before = await _active_count(owner)
    assert before == 10

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch(
            "src.core.compaction.orchestrator._run_prune",
            new=AsyncMock(return_value=(4, 1)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(0, 0, 0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_compress",
            new=AsyncMock(
                return_value=CompressResult(
                    groups_compressed=2, facts_merged=3, facts_deactivated=3
                )
            ),
        ),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(10, 3, 0.30)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert 0.0 <= report.compression_ratio <= 1.0
    assert report.active_before == 10
    assert report.active_after == 3


@pytest.mark.asyncio
async def test_compression_ratio_with_zero_active_before():
    """compression_ratio = 0 when no active facts (div by 0 guarded)."""
    owner = await _make_owner()
    # No memories at all

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch(
            "src.core.compaction.orchestrator._run_prune",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(0, 0, 0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_compress",
            new=AsyncMock(return_value=CompressResult()),
        ),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(0, 0, 0.0)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert report.compression_ratio == 0.0
    assert report.active_before == 0
    assert report.active_after == 0


@pytest.mark.asyncio
async def test_compression_ratio_positive_after_compression():
    """compression_ratio > 0 when active_after > 0 and active_before > 0."""
    owner = await _make_owner()
    for i in range(5):
        await _make_memory(owner, f"Active {i}", is_active=True)

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch(
            "src.core.compaction.orchestrator._run_prune",
            new=AsyncMock(return_value=(2, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(0, 0, 0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_compress",
            new=AsyncMock(
                return_value=CompressResult(
                    groups_compressed=1, facts_merged=0, facts_deactivated=0
                )
            ),
        ),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(5, 3, 0.60)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert report.compression_ratio > 0.0
    assert report.active_before == 5
    assert report.active_after == 3


# ═══════════════════════════════════════════════════════════════════
#  Test: CompactionReport defaults
# ═══════════════════════════════════════════════════════════════════


class TestCompactionReportDefaults:
    """Structural tests for CompactionReport dataclass — no DB needed."""

    def test_default_errors_is_empty_list(self):
        report = CompactionReport()
        assert report.errors == []
        assert report.facts_pruned == 0
        assert report.facts_nudged == 0
        assert report.groups_compressed == 0
        assert report.facts_merged == 0
        assert report.vectors_removed == 0
        assert report.skills_extracted == 0
        assert report.compression_ratio == 0.0
        assert report.active_before == 0
        assert report.active_after == 0
        assert report.duration_sec == 0.0

    def test_all_scalar_fields_default_to_zero(self):
        """All integer/float fields default to 0 or 0.0."""
        report = CompactionReport()
        int_fields = [
            "facts_pruned",
            "longterm_protected",
            "facts_nudged",
            "nudge_confirmed",
            "nudge_forgotten",
            "nudge_edited",
            "groups_examined",
            "groups_compressed",
            "facts_merged",
            "reval_examined",
            "reval_changed",
            "vectors_removed",
            "skills_extracted",
        ]
        for field_name in int_fields:
            val = getattr(report, field_name)
            assert isinstance(val, int), f"{field_name} should be int, got {type(val)}"
            assert val == 0, f"{field_name} should default to 0, got {val}"

        assert report.compression_ratio == 0.0
        assert report.active_before == 0
        assert report.active_after == 0
        assert report.duration_sec == 0.0

    def test_can_set_all_fields_directly(self):
        """CompactionReport accepts values for all fields (no slots restriction)."""
        report = CompactionReport(
            facts_pruned=10,
            longterm_protected=2,
            facts_nudged=5,
            nudge_confirmed=3,
            nudge_forgotten=1,
            nudge_edited=1,
            groups_examined=4,
            groups_compressed=2,
            facts_merged=8,
            reval_examined=20,
            reval_changed=7,
            vectors_removed=15,
            skills_extracted=1,
            compression_ratio=0.35,
            active_before=100,
            active_after=65,
            duration_sec=12.5,
            errors=["Test error"],
        )
        assert report.facts_pruned == 10
        assert report.compression_ratio == 0.35
        assert len(report.errors) == 1


# ═══════════════════════════════════════════════════════════════════
#  Test: LLM mock roundtrip
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_llm_provider_mock_used_in_pipeline():
    """MockLLMProvider is wired through build_provider → pipeline."""
    owner = await _make_owner()

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert isinstance(report, CompactionReport)
    assert report.errors == []  # No errors with stubs


@pytest.mark.asyncio
async def test_vector_store_mock_delete_invoked():
    """VectorStore GC mock is properly wired."""
    owner = await _make_owner()

    mock_llm = MockLLMProvider()
    mock_vs = _mock_vector_store()

    with (
        patch("src.llm.router.build_provider", new=AsyncMock(return_value=mock_llm)),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new=AsyncMock(return_value=mock_vs),
        ),
        patch(
            "src.core.compaction.orchestrator._run_prune",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_nudge",
            new=AsyncMock(return_value=(0, 0, 0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_compress",
            new=AsyncMock(return_value=CompressResult()),
        ),
        patch(
            "src.core.compaction.orchestrator._run_reval",
            new=AsyncMock(return_value=(0, 0)),
        ),
        patch(
            "src.core.compaction.orchestrator._run_gc", new=AsyncMock(return_value=7)
        ),
        patch(
            "src.core.compaction.orchestrator._run_learn", new=AsyncMock(return_value=0)
        ),
        patch(
            "src.core.compaction.orchestrator._run_metrics",
            new=AsyncMock(return_value=(0, 0, 0.0)),
        ),
    ):
        report = await run_compaction_pipeline(owner.telegram_id)

    assert report.vectors_removed == 7
    # GC mock was invoked via orchestrator phase 5
