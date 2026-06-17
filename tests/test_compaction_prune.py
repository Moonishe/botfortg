"""Tests for compaction_prune — Compaction Pipeline v2 two-factor prune.

Tests the logic spec:
- Active, non-pinned, non-task facts are candidates
- Longterm temporal_layer: threshold = base_threshold * longterm_mult
  (10x slower forget)
- NULL temporal_layer → treated as medium
- use_count==0 AND last_used_at is None AND age > zero_use_days
  → candidate regardless of retention
- Longterm facts NOT touched by use_count=0 rule
- Pinned facts NOT touched
- memory_type == "task" NOT touched
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, UTC

import pytest
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Environment must be set before importing src modules
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")

from src.config import settings as config_settings
from src.core.memory.auto_forget import compaction_prune
from src.db.models._base import User
from src.db.models._memory import Memory
from src.db.session import get_session

UTC = UTC

# Shared test telegram_id — each test creates its own User with this id
# Cleanup between tests ensures no cross-contamination.
TEST_TG_ID = 123456789


def _old(days: int) -> datetime:
    """Return a UTC datetime `days` in the past."""
    return datetime.now(UTC) - timedelta(days=days)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_db():
    """Recreate all tables before each test for a clean slate.

    Sync fixture using asyncio.run() to avoid MissingGreenlet on aiosqlite engine
    when disposing — the ``_db_init`` conftest pattern.
    """
    import asyncio

    from src.db.session import Base, _FTS_SETUP, _MEMORY_FTS_SETUP, engine

    async def _recreate():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            for tbl_name in ("alembic_version", "messages_fts", "memories_fts"):
                await conn.execute(text(f"DROP TABLE IF EXISTS {tbl_name}"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for stmt in _FTS_SETUP:
                await conn.execute(text(stmt))
            for stmt in _MEMORY_FTS_SETUP:
                await conn.execute(text(stmt))

    asyncio.run(_recreate())
    yield
    # ponytail: engine.sync_engine.dispose() causes MissingGreenlet inside
    # async fixtures; the shared engine is cleaned up at session teardown.


@pytest.fixture
async def _user_id() -> int:
    """Create a test User, return its id. Cleaned up by _clean_db."""
    async with get_session() as session:
        user = User(telegram_id=TEST_TG_ID)
        session.add(user)
        await session.flush()
        uid = user.id
    return uid


# ---------------------------------------------------------------------------
# Helper: build a memory record (not yet added to session)
# ---------------------------------------------------------------------------


def _make_memory(
    user_id: int,
    *,
    fact: str = "test fact",
    temporal_layer: str | None = "medium",
    use_count: int = 0,
    last_used_at: datetime | None = None,
    created_at: datetime | None = None,
    pinned: bool = False,
    memory_type: str | None = None,
    is_active: bool = True,
    decay_rate: float = 0.07,
) -> Memory:
    """Build (but don't add) a Memory record with default test-friendly values."""
    return Memory(
        user_id=user_id,
        fact=fact,
        temporal_layer=temporal_layer,
        use_count=use_count,
        last_used_at=last_used_at,
        created_at=created_at or _old(5),  # recent by default
        pinned=pinned,
        memory_type=memory_type,
        is_active=is_active,
        decay_rate=decay_rate,
        importance=0.5,
        confidence=0.5,
        source_quality=0.5,
        extraction_quality=0.5,
        source="test",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCompactionPruneLongtermProtection:
    """longterm факт с низким retention остаётся (защита)."""  # noqa: RUF002

    async def test_longterm_low_retention_stays(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            m = _make_memory(
                _user_id,
                temporal_layer="longterm",
                use_count=0,
                last_used_at=None,
                created_at=_old(60),  # retention ≈ e^(-0.07*60) ≈ 0.015 → very low
            )
            session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 0, "longterm fact must NOT be deactivated"
            assert longterm_protected == 1, "longterm fact must be counted as protected"

            await session.refresh(m)
            assert m.is_active is True, "longterm fact must remain active"


class TestCompactionPruneMediumDeactivation:
    """medium факт с низким retention и use_count=0 деактивируется."""  # noqa: RUF002

    async def test_medium_low_retention_deactivated(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            m = _make_memory(
                _user_id,
                temporal_layer="medium",
                use_count=0,
                last_used_at=None,
                created_at=_old(60),  # retention ≈ 0.015 < 0.15
            )
            session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 1, (
                "medium fact with low retention must be deactivated"
            )
            assert longterm_protected == 0

            await session.refresh(m)
            assert m.is_active is False, "medium fact must be inactive after prune"


class TestCompactionPrunePinned:
    """pinned факт не деактивируется."""

    async def test_pinned_not_deactivated(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            m = _make_memory(
                _user_id,
                temporal_layer="medium",
                use_count=0,
                last_used_at=None,
                created_at=_old(60),
                pinned=True,
                fact="pinned-test",
            )
            session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 0, "pinned fact must NOT be deactivated"
            assert longterm_protected == 0

            await session.refresh(m)
            assert m.is_active is True, "pinned fact must stay active"


class TestCompactionPruneTask:
    """task факт не деактивируется."""

    async def test_task_not_deactivated(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            m = _make_memory(
                _user_id,
                temporal_layer="medium",
                use_count=0,
                last_used_at=None,
                created_at=_old(60),
                memory_type="task",
                fact="task-test",
            )
            session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 0, "task fact must NOT be deactivated"
            assert longterm_protected == 0

            await session.refresh(m)
            assert m.is_active is True, "task fact must stay active"


class TestCompactionPruneNullTemporalLayer:
    """NULL temporal_layer → medium, деактивируется при низком retention."""

    async def test_null_layer_treated_as_medium(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            m = _make_memory(
                _user_id,
                temporal_layer=None,  # NULL — must be treated as medium
                use_count=0,
                last_used_at=None,
                created_at=_old(60),
                fact="null-layer-test",
            )
            session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 1, (
                "NULL temporal_layer must be treated as medium → deactivated"
            )
            assert longterm_protected == 0

            await session.refresh(m)
            assert m.is_active is False, "NULL layer fact must be inactive"


class TestCompactionPruneZeroUseAutoForget:
    """use_count=0 + last_used_at=None + age > 30 дней деактивируется."""

    async def test_zero_use_old_deactivated(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            m = _make_memory(
                _user_id,
                temporal_layer="medium",
                use_count=0,
                last_used_at=None,
                created_at=_old(31),  # just over zero_use_days default (30)
                fact="zero-use-old-test",
            )
            session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 1, (
                "use_count=0 + age>30 must be deactivated regardless of retention"
            )
            assert longterm_protected == 0

            await session.refresh(m)
            assert m.is_active is False


class TestCompactionPruneEdgeCases:
    """Edge cases and regression guards."""

    async def test_active_fact_with_use_count_stays(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        """Fact with use_count > 0 and last_used_at set should NOT be deactivated."""
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            m = _make_memory(
                _user_id,
                temporal_layer="medium",
                use_count=5,
                last_used_at=_old(1),  # used yesterday → high access boost
                created_at=_old(60),
                fact="active-fact",
            )
            session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 0, "frequently used fact must stay active"
            assert longterm_protected == 0

            await session.refresh(m)
            assert m.is_active is True

    async def test_mixed_scenario(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        """Mix of deactivatable and protected facts → correct counts."""
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            facts = [
                # Should be deactivated: medium, old, use_count=0
                _make_memory(
                    _user_id,
                    fact="medium-old-1",
                    temporal_layer="medium",
                    use_count=0,
                    last_used_at=None,
                    created_at=_old(60),
                ),
                _make_memory(
                    _user_id,
                    fact="medium-old-2",
                    temporal_layer="medium",
                    use_count=0,
                    last_used_at=None,
                    created_at=_old(45),
                ),
                # Should be protected: longterm
                _make_memory(
                    _user_id,
                    fact="longterm-old",
                    temporal_layer="longterm",
                    use_count=0,
                    last_used_at=None,
                    created_at=_old(60),
                ),
                # Should NOT be touched: pinned
                _make_memory(
                    _user_id,
                    fact="pinned-old",
                    temporal_layer="medium",
                    use_count=0,
                    last_used_at=None,
                    created_at=_old(60),
                    pinned=True,
                ),
                # Should NOT be touched: task type
                _make_memory(
                    _user_id,
                    fact="task-old",
                    temporal_layer="medium",
                    use_count=0,
                    last_used_at=None,
                    created_at=_old(60),
                    memory_type="task",
                ),
            ]
            session.add_all(facts)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 2, "only 2 medium facts should be deactivated"
            assert longterm_protected == 1, "1 longterm fact was protected"

    async def test_disabled_setting_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        """When auto_forget is disabled, no deactivations occur."""
        monkeypatch.setattr(config_settings, "auto_forget_enabled", False)

        async with get_session() as session:
            m = _make_memory(
                _user_id,
                temporal_layer="medium",
                use_count=0,
                last_used_at=None,
                created_at=_old(60),
                fact="disabled-test",
            )
            session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 0, "no deactivations when auto_forget is disabled"
            assert longterm_protected == 0

            await session.refresh(m)
            assert m.is_active is True, "fact must stay active when disabled"

    async def test_zero_longterm_mult_guarded(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        """longterm_mult=0 does NOT cause ZeroDivisionError in compute_retention."""
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            m = _make_memory(
                _user_id,
                temporal_layer="longterm",
                use_count=0,
                last_used_at=None,
                created_at=_old(60),
                fact="zero-mult-test",
            )
            session.add(m)
            await session.flush()

            # Should not raise — decay_multiplier=0 guarded in compute_retention
            deactivated, longterm_protected = await compaction_prune(
                session, _user_id, longterm_mult=0.0
            )

            # With longterm_mult=0, decay_rate/0 is skipped → retention stays high
            # So longterm facts should not be pruned
            assert isinstance(deactivated, int)
            assert isinstance(longterm_protected, int)

    async def test_all_pinned_facts_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        """When all facts are pinned, no deactivations occur."""
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            for i in range(3):
                m = _make_memory(
                    _user_id,
                    fact=f"pinned-{i}",
                    temporal_layer="medium",
                    use_count=0,
                    last_used_at=None,
                    created_at=_old(60),
                    pinned=True,
                )
                session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 0, "All pinned → nothing pruned"
            assert longterm_protected == 0

    async def test_all_inactive_facts_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        """When all facts are already inactive, no deactivations occur."""
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            for i in range(3):
                m = _make_memory(
                    _user_id,
                    fact=f"inactive-{i}",
                    temporal_layer="medium",
                    use_count=0,
                    last_used_at=None,
                    created_at=_old(60),
                    is_active=False,
                )
                session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)

            assert deactivated == 0, "All inactive → nothing pruned"
            assert longterm_protected == 0

    async def test_partially_null_use_count_defaults_to_zero(
        self, monkeypatch: pytest.MonkeyPatch, _user_id: int
    ) -> None:
        """Memory with use_count=None is treated as use_count=0."""
        monkeypatch.setattr(config_settings, "auto_forget_enabled", True)
        monkeypatch.setattr(config_settings, "auto_forget_threshold", 0.15)

        async with get_session() as session:
            m = _make_memory(
                _user_id,
                temporal_layer="medium",
                use_count=None,  # type: ignore[arg-type]
                last_used_at=None,
                created_at=_old(60),
                fact="null-use-count",
            )
            session.add(m)
            await session.flush()

            deactivated, longterm_protected = await compaction_prune(session, _user_id)
            # use_count=None → or 0 → 0, low retention → should be pruned
            assert deactivated == 1, "NULL use_count treated as 0 → should prune"
            assert longterm_protected == 0
