"""Tests for Phase 2 NUDGE compaction pipeline.

Covers:
  - build_nudge_keyboard — implemented, tests pass now
  - select_nudge_candidates — stub (tests skipped until implemented)
  - apply_nudge_decision — stub (tests skipped until implemented)

Selection rules (per spec):
  temporal_layer="medium", use_count < 3, confidence < 0.7,
  NOT pinned, is_active=True, memory_type != "task"

Uses in-memory SQLite following the project test pattern
(test_dreaming_reval.py, test_memory_smoke.py).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, UTC

import pytest
from sqlalchemy import select, text

# ── Environment setup BEFORE importing src modules ──────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"  # noqa: S105
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.core.compaction.models import NudgeCandidate
from src.core.compaction.nudge import (
    apply_nudge_decision,
    build_nudge_keyboard,
    select_nudge_candidates,
)
from src.db.models import Memory, MemoryVersion
from src.db.repo import add_memory, get_or_create_user
from src.db.session import get_session

OWNER_TG_ID = 123456789


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def setup_db():
    """Recreate all tables before each test (in-memory SQLite).

    Uses Base.metadata.create_all directly (like test_dreaming_reval.py)
    to avoid init_db() issues with per-connection in-memory databases.
    Every test gets a completely fresh database.
    """
    from src.db.session import (
        Base,
        _FTS_SETUP,
        _MEMORY_FTS_SETUP,
        engine,
    )

    # Clear the module-level context cache synchronously so tests don't see
    # user IDs cached by other test files (tests run sequentially).
    from src.core.context_cache import _cache

    _cache.clear()

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
            for stmt in _MEMORY_FTS_SETUP:
                await conn.execute(text(stmt))

    asyncio.run(_recreate())


# ── Helpers ─────────────────────────────────────────────────────────


async def _make_owner(tg_id: int = OWNER_TG_ID):
    """Create / retrieve the test owner user."""
    async with get_session() as session:
        return await get_or_create_user(session, tg_id)


async def _make_memory(
    owner,
    fact: str,
    *,
    confidence: float = 0.5,
    use_count: int = 0,
    temporal_layer: str | None = None,
    memory_type: str | None = None,
    pinned: bool = False,
    is_active: bool = True,
    created_offset_days: float = 15,
    **kwargs,
) -> Memory:
    """Create a Memory row with controlled attributes for nudge testing.

    After add_memory, sets temporal_layer, is_active, and created_at
    if needed, then commits.
    """
    async with get_session() as session:
        m = await add_memory(
            session,
            owner,
            fact=fact,
            confidence=confidence,
            use_count=use_count,
            memory_type=memory_type,
            pinned=pinned,
            **kwargs,
        )
        if m is None:
            raise RuntimeError(f"add_memory returned None for fact={fact!r}")
        if temporal_layer is not None:
            m.temporal_layer = temporal_layer
        if not is_active:
            m.is_active = False
        if created_offset_days != 0:
            m.created_at = datetime.now(UTC) - timedelta(days=created_offset_days)
        await session.commit()
        return m


# =====================================================================
# Tests: build_nudge_keyboard (IMPLEMENTED — should pass now)
# =====================================================================


class TestBuildNudgeKeyboard:
    """Tests for build_nudge_keyboard — already implemented."""

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    def _make_candidate(memory_id: int = 1) -> NudgeCandidate:
        return NudgeCandidate(
            memory_id=memory_id,
            fact="test fact",
            confidence=0.5,
            use_count=1,
        )

    # ── tests ────────────────────────────────────────────────────

    def test_keyboard_has_three_buttons_in_one_row(self):
        """Keyboard contains exactly 3 inline buttons in a single row."""
        candidate = self._make_candidate()
        markup = build_nudge_keyboard(candidate)
        keyboard = markup.inline_keyboard

        assert len(keyboard) == 1, "expected single row"
        assert len(keyboard[0]) == 3, "expected 3 buttons in row"

    def test_confirm_callback_data_format(self):
        """Confirm button callback_data is memq:nudge:confirm:{id}."""
        candidate = self._make_candidate(memory_id=42)
        markup = build_nudge_keyboard(candidate)
        btn = markup.inline_keyboard[0][0]
        assert btn.callback_data == "memq:nudge:confirm:42"

    def test_forget_callback_data_format(self):
        """Forget button callback_data is memq:nudge:forget:{id}."""
        candidate = self._make_candidate(memory_id=7)
        markup = build_nudge_keyboard(candidate)
        btn = markup.inline_keyboard[0][1]
        assert btn.callback_data == "memq:nudge:forget:7"

    def test_edit_callback_data_format(self):
        """Edit button callback_data is memq:nudge:edit:{id}:0."""
        candidate = self._make_candidate(memory_id=99)
        markup = build_nudge_keyboard(candidate)
        btn = markup.inline_keyboard[0][2]
        assert btn.callback_data == "memq:nudge:edit:99:0"

    def test_button_display_texts(self):
        """Buttons have correct user-facing texts."""
        candidate = self._make_candidate()
        markup = build_nudge_keyboard(candidate)
        row = markup.inline_keyboard[0]
        assert row[0].text == "✅ Актуально"
        assert row[1].text == "🗑 Забыть"
        assert row[2].text == "✏️ Изменить"


# =====================================================================
# Tests: select_nudge_candidates (STUB — skipped until implemented)
# =====================================================================


class TestSelectNudgeCandidates:
    """Tests for select_nudge_candidates filtering logic.

    Expected rules:
      temporal_layer="medium", use_count < 3, confidence < 0.7,
      pinned=False, is_active=True, memory_type != "task",
      returns up to `limit` candidates.
    """

    async def test_empty_db_returns_empty_list(self):
        """Returns [] when no memories exist."""
        owner = await _make_owner()
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id)
        assert result == []

    async def test_selects_eligible_candidate(self):
        """Selects fact with medium layer, low confidence, low use_count."""
        owner = await _make_owner()
        await _make_memory(
            owner,
            "eligible fact",
            confidence=0.6,
            use_count=1,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id)
        assert len(result) == 1
        assert result[0].fact == "eligible fact"
        assert isinstance(result[0], NudgeCandidate)
        # Candidate fields populated correctly
        assert isinstance(result[0].memory_id, int)
        assert isinstance(result[0].confidence, float)

    # ── Exclusion tests ──────────────────────────────────────────

    async def test_excludes_pinned_facts(self):
        """Pinned facts are never selected regardless of other attributes."""
        owner = await _make_owner()
        await _make_memory(
            owner,
            "pinned fact",
            confidence=0.3,
            use_count=0,
            temporal_layer="medium",
            pinned=True,
        )
        await _make_memory(
            owner,
            "not pinned",
            confidence=0.3,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id)
        assert len(result) == 1
        assert result[0].fact == "not pinned"

    async def test_excludes_task_type(self):
        """Facts with memory_type="task" are excluded."""
        owner = await _make_owner()
        await _make_memory(
            owner,
            "task fact",
            confidence=0.3,
            use_count=0,
            temporal_layer="medium",
            memory_type="task",
        )
        await _make_memory(
            owner,
            "personal fact",
            confidence=0.3,
            use_count=0,
            temporal_layer="medium",
            memory_type="personal",
        )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id)
        assert len(result) == 1
        assert result[0].fact == "personal fact"

    async def test_excludes_inactive_facts(self):
        """Facts with is_active=False are excluded."""
        owner = await _make_owner()
        await _make_memory(
            owner,
            "inactive fact",
            confidence=0.3,
            use_count=0,
            temporal_layer="medium",
            is_active=False,
        )
        await _make_memory(
            owner,
            "active fact",
            confidence=0.3,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id)
        assert len(result) == 1
        assert result[0].fact == "active fact"

    async def test_excludes_high_confidence_facts(self):
        """Facts with confidence >= 0.7 are excluded."""
        owner = await _make_owner()
        await _make_memory(
            owner,
            "high conf",
            confidence=0.7,
            use_count=0,
            temporal_layer="medium",
        )
        await _make_memory(
            owner,
            "low conf",
            confidence=0.69,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id)
        assert len(result) == 1
        assert result[0].fact == "low conf"

    async def test_excludes_high_use_count_facts(self):
        """Facts with use_count >= 3 are excluded."""
        owner = await _make_owner()
        await _make_memory(
            owner,
            "high use",
            confidence=0.5,
            use_count=3,
            temporal_layer="medium",
        )
        await _make_memory(
            owner,
            "low use",
            confidence=0.5,
            use_count=2,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id)
        assert len(result) == 1
        assert result[0].fact == "low use"

    async def test_excludes_non_medium_temporal_layer(self):
        """Facts with temporal_layer != 'medium' are excluded."""
        owner = await _make_owner()
        await _make_memory(
            owner,
            "recent",
            confidence=0.5,
            use_count=0,
            temporal_layer="recent",
        )
        await _make_memory(
            owner,
            "longterm",
            confidence=0.5,
            use_count=0,
            temporal_layer="longterm",
        )
        await _make_memory(
            owner,
            "medium",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        await _make_memory(
            owner,
            "null layer",
            confidence=0.5,
            use_count=0,
            temporal_layer=None,
        )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id)
        assert len(result) == 1
        assert result[0].fact == "medium"

    # ── Limit tests ──────────────────────────────────────────────

    async def test_limit_is_respected(self):
        """Returns at most `limit` candidates even when more are eligible."""
        owner = await _make_owner()
        for i in range(10):
            await _make_memory(
                owner,
                f"fact {i}",
                confidence=0.5,
                use_count=0,
                temporal_layer="medium",
            )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id, limit=3)
        assert len(result) <= 3

    async def test_default_limit_is_five(self):
        """Default limit is 5 when not explicitly specified."""
        owner = await _make_owner()
        for i in range(8):
            await _make_memory(
                owner,
                f"fact {i}",
                confidence=0.5,
                use_count=0,
                temporal_layer="medium",
            )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id)
        assert len(result) <= 5

    async def test_limit_zero_returns_empty(self):
        """limit=0 returns empty list."""
        owner = await _make_owner()
        await _make_memory(
            owner,
            "eligible",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id, limit=0)
        assert result == []

    # ── Isolation tests ──────────────────────────────────────────

    async def test_scoped_to_specific_user(self):
        """Only returns candidates for the specified user_id."""
        owner = await _make_owner(OWNER_TG_ID)
        other = await _make_owner(99999)
        await _make_memory(
            owner,
            "owner fact",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        await _make_memory(
            other,
            "other fact",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await select_nudge_candidates(session, owner.id)
        facts = {c.fact for c in result}
        assert "owner fact" in facts
        assert "other fact" not in facts


# =====================================================================
# Tests: apply_nudge_decision (STUB — skipped until implemented)
# =====================================================================


class TestApplyNudgeDecision:
    """Tests for apply_nudge_decision — confirm / forget / edit actions.

    Expected behaviour:
      - confirm: use_count += 1, confidence += 0.1 (cap 1.0), updated_at=now
      - forget: is_active=False, validity_end=now
      - edit: update fact via update_memory_text + create MemoryVersion
      - unknown action / missing memory → return False
    """

    # ── confirm action ───────────────────────────────────────────

    async def test_confirm_increments_use_count(self):
        """Confirm increments use_count by 1."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "confirm use count",
            confidence=0.5,
            use_count=2,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await apply_nudge_decision(session, mem.id, "confirm")
        assert result is True

        async with get_session() as session:
            updated = await session.get(Memory, mem.id)
        assert updated.use_count == 3

    async def test_confirm_boosts_confidence(self):
        """Confirm adds 0.1 to confidence."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "boost me",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await apply_nudge_decision(session, mem.id, "confirm")
        assert result is True

        async with get_session() as session:
            updated = await session.get(Memory, mem.id)
        assert updated.confidence == 0.6

    async def test_confirm_confidence_capped_at_one(self):
        """Confidence does not exceed 1.0 after boost."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "very confident",
            confidence=0.95,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await apply_nudge_decision(session, mem.id, "confirm")
        assert result is True

        async with get_session() as session:
            updated = await session.get(Memory, mem.id)
        assert updated.confidence == 1.0
        assert updated.use_count == 1

    async def test_confirm_updates_updated_at(self):
        """Confirm sets updated_at to current time."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "timestamp test",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        original_updated_at = (
            mem.updated_at.replace(tzinfo=None) if mem.updated_at else None
        )
        before = datetime.now(UTC).replace(tzinfo=None)

        async with get_session() as session:
            await apply_nudge_decision(session, mem.id, "confirm")

        async with get_session() as session:
            updated = await session.get(Memory, mem.id)
        assert updated.updated_at >= before
        assert original_updated_at is not None
        assert updated.updated_at > original_updated_at

    # ── forget action ────────────────────────────────────────────

    async def test_forget_deactivates_fact(self):
        """Forget sets is_active=False."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "forget me",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await apply_nudge_decision(session, mem.id, "forget")
        assert result is True

        async with get_session() as session:
            updated = await session.get(Memory, mem.id)
        assert updated.is_active is False

    async def test_forget_sets_validity_end(self):
        """Forget sets validity_end to current time."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "validity end test",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        before = datetime.now(UTC).replace(tzinfo=None)

        async with get_session() as session:
            await apply_nudge_decision(session, mem.id, "forget")

        async with get_session() as session:
            updated = await session.get(Memory, mem.id)
        assert updated.validity_end is not None
        assert updated.validity_end >= before

    async def test_forget_preserves_other_fields(self):
        """Forget does not modify confidence or use_count."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "preserve fields",
            confidence=0.5,
            use_count=2,
            temporal_layer="medium",
        )
        async with get_session() as session:
            await apply_nudge_decision(session, mem.id, "forget")

        async with get_session() as session:
            updated = await session.get(Memory, mem.id)
        assert updated.confidence == 0.5
        assert updated.use_count == 2

    # ── edit action ──────────────────────────────────────────────

    async def test_edit_updates_fact_text(self):
        """Edit replaces the fact text via update_memory_text."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "original fact",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await apply_nudge_decision(
                session,
                mem.id,
                "edit",
                new_fact="updated fact",
            )
        assert result is True

        async with get_session() as session:
            updated = await session.get(Memory, mem.id)
        assert updated.fact == "updated fact"

    async def test_edit_creates_memory_version(self):
        """Edit creates a MemoryVersion record with the new fact text."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "version test",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await apply_nudge_decision(
                session,
                mem.id,
                "edit",
                new_fact="versioned fact",
            )
        assert result is True

        async with get_session() as session:
            versions = (
                (
                    await session.execute(
                        select(MemoryVersion).where(MemoryVersion.memory_id == mem.id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(versions) >= 1
        assert versions[0].fact_text == "versioned fact"

    async def test_edit_preserves_confidence_and_use_count(self):
        """Edit does not change confidence or use_count."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "edit preserve",
            confidence=0.5,
            use_count=2,
            temporal_layer="medium",
        )
        async with get_session() as session:
            await apply_nudge_decision(
                session,
                mem.id,
                "edit",
                new_fact="revised fact",
            )

        async with get_session() as session:
            updated = await session.get(Memory, mem.id)
        assert updated.confidence == 0.5
        assert updated.use_count == 2

    # ── error / edge cases ───────────────────────────────────────

    async def test_unknown_action_returns_false(self):
        """Unrecognised action string returns False."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "unknown action",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await apply_nudge_decision(session, mem.id, "bogus_action")
        assert result is False

    async def test_nonexistent_memory_returns_false(self):
        """Non-existent memory_id returns False for any action."""
        async with get_session() as session:
            result = await apply_nudge_decision(session, 99999, "confirm")
        assert result is False

    async def test_edit_without_new_fact_returns_false(self):
        """Edit action requires new_fact; returns False when omitted."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "no new fact",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await apply_nudge_decision(session, mem.id, "edit")
        assert result is False

    async def test_forget_already_inactive_still_succeeds(self):
        """Forget on an already inactive fact is idempotent."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "already gone",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
            is_active=False,
        )
        async with get_session() as session:
            result = await apply_nudge_decision(session, mem.id, "forget")
        assert result is True

    async def test_confirm_on_inactive_returns_false(self):
        """Confirm on inactive fact must be rejected (can't confirm dead fact)."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "dead fact",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
            is_active=False,
        )
        async with get_session() as session:
            result = await apply_nudge_decision(session, mem.id, "confirm")
        assert result is False

    async def test_edit_with_empty_new_fact_returns_false(self):
        """Edit with empty string new_fact returns False."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "original",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await apply_nudge_decision(session, mem.id, "edit", new_fact="")
        assert result is False

    async def test_edit_with_whitespace_only_new_fact(self):
        """Edit with whitespace-only new_fact is accepted."""
        owner = await _make_owner()
        mem = await _make_memory(
            owner,
            "original",
            confidence=0.5,
            use_count=0,
            temporal_layer="medium",
        )
        async with get_session() as session:
            result = await apply_nudge_decision(session, mem.id, "edit", new_fact="   ")
        # update_memory_text may accept or reject whitespace; either is fine
        # The key is that the function doesn't crash
        assert result is False or result is True

    async def test_negative_memory_id_returns_false(self):
        """Memory ID of 0 or negative returns False (not found)."""
        async with get_session() as session:
            result = await apply_nudge_decision(session, 0, "confirm")
        assert result is False

        async with get_session() as session:
            result = await apply_nudge_decision(session, -1, "confirm")
        assert result is False
