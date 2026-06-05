"""Tests for Dreaming V3 semantic re-evaluation of stale facts.

Covers:
  - _parse_reval_response (JSON parsing, validation, edge cases)
  - select_stale_facts_for_reval (DB filters: pinned, confidence, limit, etc.)
  - apply_reval_result (past/permanent/invalid/skip, supersedes links)
  - rollback_recent_revals (restore + cleanup)
  - revaluate_fact (LLM call + parsing integration)
  - revaluation_run (end-to-end with MockLLMProvider)

Uses in-memory SQLite following the project test pattern
(test_memory_smoke.py, test_supersedes_chain.py).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

# ── Environment setup BEFORE importing src modules ──────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.core.memory.dreaming_reval import (
    _parse_reval_response,
    _build_user_prompt,
    _ALLOWED_ACTIONS,
    _ALLOWED_MEMORY_TYPES,
    select_stale_facts_for_reval,
    apply_reval_result,
    deactivate_memory,
    add_supersedes_link,
    reval_fact,
    reval_run,
    rollback_recent_revals,
    RevalResult,
    RevalBatchSummary,
    reval_summary_text,
)
from src.db.session import get_session, init_db
from src.db.repo import add_memory, get_or_create_user
from src.db.models import Memory, MemoryLink
from src.llm.base import ChatMessage, TaskType

OWNER_TG_ID = 123456789


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def setup_db():
    """Recreate all tables before each test (in-memory SQLite).

    Uses Base.metadata.create_all directly (like conftest._db_init)
    to avoid init_db() issues with per-connection in-memory databases.
    Every test gets a completely fresh database.
    """
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


# ── Helpers ─────────────────────────────────────────────────────────


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
    created_offset_days: float = 0,
    decay_rate: float | None = None,
    source: str = "chat",
    **kwargs,
) -> Memory:
    """Create a Memory row with controlled values.

    After add_memory, sets created_at (offset from now in days)
    and is_active if needed, then commits.
    """
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
        if created_offset_days != 0:
            new_ts = datetime.now(timezone.utc) - timedelta(days=created_offset_days)
            m.created_at = new_ts
        if not is_active:
            m.is_active = False
        await session.commit()
        return m


# ── Mock LLM Provider ───────────────────────────────────────────────


class MockLLMProvider:
    """Mock that returns pre-configured JSON responses.

    Implements the LLMProvider protocol minimally: ``chat`` + ``close``.
    """

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.call_count = 0
        self.name = "mock"

    async def chat(self, messages, *, heavy=False, task_type="default"):
        if self.call_count >= len(self.responses):
            return json.dumps({"action": "skip", "reason": "no more responses"})
        resp = self.responses[self.call_count]
        self.call_count += 1
        return resp

    async def close(self):
        pass


# ═══════════════════════════════════════════════════════════════════
#  Tests: _parse_reval_response
# ═══════════════════════════════════════════════════════════════════


class TestParseRevalResponse:
    """Unit tests for JSON response parser — no DB needed."""

    # 1 ─ valid JSON, action='past'
    def test_valid_json_past_action(self):
        response = json.dumps(
            {
                "action": "past",
                "updated_fact": "Пользователь съездил в Сингапур в июле 2026",
                "new_memory_type": "personal",
                "decay_rate": 0.03,
                "reason": "событие уже произошло",
            }
        )
        result = _parse_reval_response(response)
        assert result is not None
        assert result["action"] == "past"
        assert result["updated_fact"] == "Пользователь съездил в Сингапур в июле 2026"
        assert result["new_memory_type"] == "personal"
        assert result["decay_rate"] == 0.03
        assert result["reason"] == "событие уже произошло"

    # 2 ─ action='skip'
    def test_skip_action(self):
        response = json.dumps({"action": "skip", "reason": "факт актуален"})
        result = _parse_reval_response(response)
        assert result is not None
        assert result["action"] == "skip"
        assert result["reason"] == "факт актуален"

    # 3 ─ invalid action → None
    def test_invalid_action_returns_none(self):
        response = json.dumps({"action": "delete", "reason": "bad"})
        result = _parse_reval_response(response)
        assert result is None

    # 4 ─ malformed JSON → None (no crash)
    def test_malformed_json_returns_none(self):
        result = _parse_reval_response("{not valid json!!!")
        assert result is None

    # 5 ─ markdown-wrapped JSON ```json ... ```
    def test_markdown_fenced_json(self):
        response = (
            "```json\n"
            '{"action": "permanent", "updated_fact": "Новый факт навсегда", '
            '"new_memory_type": "personal", "decay_rate": 0.01, '
            '"reason": "важный факт"}\n'
            "```"
        )
        result = _parse_reval_response(response)
        assert result is not None
        assert result["action"] == "permanent"
        assert result["updated_fact"] == "Новый факт навсегда"

    # 5b ─ markdown without language tag
    def test_markdown_no_lang_tag(self):
        response = '```\n{"action": "invalid", "reason": "устарел"}\n```'
        result = _parse_reval_response(response)
        assert result is not None
        assert result["action"] == "invalid"

    # ─ edge cases ─

    def test_none_or_empty_returns_none(self):
        assert _parse_reval_response(None) is None
        assert _parse_reval_response("") is None
        assert _parse_reval_response("   ") is None

    def test_decay_rate_clamped_too_high(self):
        response = json.dumps({"action": "past", "decay_rate": 0.99, "reason": "test"})
        result = _parse_reval_response(response)
        assert result["decay_rate"] == 0.30

    def test_decay_rate_clamped_too_low(self):
        response = json.dumps({"action": "past", "decay_rate": 0.001, "reason": "test"})
        result = _parse_reval_response(response)
        assert result["decay_rate"] == 0.01

    def test_decay_rate_non_numeric_dropped(self):
        response = json.dumps(
            {"action": "past", "decay_rate": "fast", "reason": "test"}
        )
        result = _parse_reval_response(response)
        assert result["decay_rate"] is None

    def test_invalid_memory_type_dropped(self):
        response = json.dumps(
            {
                "action": "permanent",
                "new_memory_type": "hallucinated",
                "reason": "test",
            }
        )
        result = _parse_reval_response(response)
        assert result is not None
        assert result["new_memory_type"] is None

    def test_valid_memory_types_accepted(self):
        for mt in _ALLOWED_MEMORY_TYPES:
            response = json.dumps(
                {"action": "past", "new_memory_type": mt, "reason": "test"}
            )
            result = _parse_reval_response(response)
            assert result["new_memory_type"] == mt

    def test_updated_fact_too_short_dropped(self):
        response = json.dumps(
            {"action": "past", "updated_fact": "AB", "reason": "test"}
        )
        result = _parse_reval_response(response)
        assert result["updated_fact"] is None

    def test_updated_fact_too_long_dropped(self):
        long_fact = "X" * 501
        response = json.dumps(
            {"action": "past", "updated_fact": long_fact, "reason": "test"}
        )
        result = _parse_reval_response(response)
        assert result["updated_fact"] is None

    def test_updated_fact_at_max_length_accepted(self):
        fact_500 = "А" * 500
        response = json.dumps(
            {"action": "past", "updated_fact": fact_500, "reason": "test"}
        )
        result = _parse_reval_response(response)
        assert result["updated_fact"] == fact_500

    def test_reason_truncated_to_120_chars(self):
        response = json.dumps({"action": "skip", "reason": "R" * 200})
        result = _parse_reval_response(response)
        assert len(result["reason"]) == 120

    def test_non_dict_returns_none(self):
        assert _parse_reval_response("[1, 2, 3]") is None
        assert _parse_reval_response("42") is None

    def test_all_whitelisted_actions_accepted(self):
        for action in _ALLOWED_ACTIONS:
            response = json.dumps({"action": action, "reason": "test"})
            result = _parse_reval_response(response)
            assert result is not None
            assert result["action"] == action


# ═══════════════════════════════════════════════════════════════════
#  Tests: select_stale_facts_for_reval
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_select_stale_facts_filters_pinned():
    """Pinned facts are excluded even if they match other criteria."""
    owner = await _make_owner()
    await _make_memory(
        owner,
        "Закреплённый",
        memory_type="temporary",
        pinned=True,
        confidence=0.9,
        created_offset_days=10,
    )
    await _make_memory(
        owner,
        "Обычный",
        memory_type="temporary",
        pinned=False,
        confidence=0.9,
        created_offset_days=10,
    )

    async with get_session() as session:
        facts = await select_stale_facts_for_reval(session, owner.id, limit=10)

    assert len(facts) == 1
    assert facts[0].fact == "Обычный"


@pytest.mark.asyncio
async def test_select_stale_facts_filters_low_confidence():
    """Facts with confidence < threshold are excluded."""
    owner = await _make_owner()
    await _make_memory(
        owner,
        "Низкая уверенность",
        memory_type="temporary",
        confidence=0.3,
        created_offset_days=10,
    )
    await _make_memory(
        owner,
        "Высокая уверенность",
        memory_type="temporary",
        confidence=0.8,
        created_offset_days=10,
    )
    await _make_memory(
        owner,
        "Ровно порог",
        memory_type="temporary",
        confidence=0.5,
        created_offset_days=10,
    )

    async with get_session() as session:
        facts = await select_stale_facts_for_reval(
            session,
            owner.id,
            limit=10,
            confidence_threshold=0.5,
        )

    facts_text = {f.fact for f in facts}
    assert "Высокая уверенность" in facts_text
    assert "Ровно порог" in facts_text
    assert "Низкая уверенность" not in facts_text
    assert len(facts) == 2


@pytest.mark.asyncio
async def test_select_stale_facts_respects_limit():
    """Returns at most `limit` facts."""
    owner = await _make_owner()
    for i in range(10):
        await _make_memory(
            owner,
            f"Факт {i}",
            memory_type="temporary",
            confidence=0.9,
            created_offset_days=10 + i,
        )

    async with get_session() as session:
        facts = await select_stale_facts_for_reval(session, owner.id, limit=5)

    assert len(facts) == 5


@pytest.mark.asyncio
async def test_select_stale_facts_only_temporary_and_task():
    """Only memory_type IN ('temporary', 'task') are selected."""
    owner = await _make_owner()
    await _make_memory(
        owner,
        "temp",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )
    await _make_memory(
        owner,
        "task",
        memory_type="task",
        confidence=0.9,
        created_offset_days=10,
    )
    await _make_memory(
        owner,
        "personal",
        memory_type="personal",
        confidence=0.9,
        created_offset_days=10,
    )
    await _make_memory(
        owner,
        "contact",
        memory_type="contact_fact",
        confidence=0.9,
        created_offset_days=10,
    )

    async with get_session() as session:
        facts = await select_stale_facts_for_reval(session, owner.id, limit=10)

    types = {f.memory_type for f in facts}
    assert types <= {"temporary", "task"}
    assert len(facts) == 2


@pytest.mark.asyncio
async def test_select_stale_facts_excludes_inactive():
    """is_active=False facts are excluded."""
    owner = await _make_owner()
    await _make_memory(
        owner,
        "Активный",
        memory_type="temporary",
        confidence=0.9,
        is_active=True,
        created_offset_days=10,
    )
    await _make_memory(
        owner,
        "Неактивный",
        memory_type="temporary",
        confidence=0.9,
        is_active=False,
        created_offset_days=10,
    )

    async with get_session() as session:
        facts = await select_stale_facts_for_reval(session, owner.id, limit=10)

    assert len(facts) == 1
    assert facts[0].fact == "Активный"


@pytest.mark.asyncio
async def test_select_stale_facts_excludes_fresh():
    """Facts created within the last 7 days are excluded."""
    owner = await _make_owner()
    await _make_memory(
        owner,
        "Свежий факт",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=3,
    )
    await _make_memory(
        owner,
        "Старый факт",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )

    async with get_session() as session:
        facts = await select_stale_facts_for_reval(session, owner.id, limit=10)

    assert len(facts) == 1
    assert facts[0].fact == "Старый факт"


@pytest.mark.asyncio
async def test_select_stale_facts_lookback_filter():
    """Facts older than lookback_days are excluded."""
    owner = await _make_owner()
    await _make_memory(
        owner,
        "Очень старый",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=400,
    )
    await _make_memory(
        owner,
        "В окне lookback",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=30,
    )

    async with get_session() as session:
        facts = await select_stale_facts_for_reval(
            session,
            owner.id,
            limit=10,
            lookback_days=100,
        )

    assert len(facts) == 1
    assert facts[0].fact == "В окне lookback"


@pytest.mark.asyncio
async def test_select_stale_facts_returns_empty_when_none_match():
    """Returns empty list when no facts match criteria."""
    owner = await _make_owner()
    # No facts at all
    async with get_session() as session:
        facts = await select_stale_facts_for_reval(session, owner.id, limit=10)

    assert facts == []


# ═══════════════════════════════════════════════════════════════════
#  Tests: deactivate_memory
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_deactivate_memory_sets_inactive():
    """deactivate_memory sets is_active=False."""
    owner = await _make_owner()
    mem = await _make_memory(owner, "Факт", created_offset_days=10)

    async with get_session() as session:
        await deactivate_memory(session, mem.id, reason="test")
        await session.commit()

    async with get_session() as session:
        m = await session.get(Memory, mem.id)
        assert m.is_active is False


@pytest.mark.asyncio
async def test_deactivate_memory_nonexistent_no_error():
    """deactivate_memory on nonexistent ID does not crash."""
    async with get_session() as session:
        # Should not raise
        await deactivate_memory(session, 99999, reason="test")


# ═══════════════════════════════════════════════════════════════════
#  Tests: add_supersedes_link
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_add_supersedes_link_creates_link():
    """Creates a MemoryLink with relation_type='supersedes'."""
    owner = await _make_owner()
    a = await _make_memory(owner, "Факт A", created_offset_days=20)
    b = await _make_memory(owner, "Факт B", created_offset_days=10)

    async with get_session() as session:
        link = await add_supersedes_link(
            session,
            owner.id,
            old_id=a.id,
            new_id=b.id,
        )
        await session.commit()

    assert link is not None
    assert link.relation_type == "supersedes"
    assert link.source_id == a.id
    assert link.target_id == b.id


@pytest.mark.asyncio
async def test_add_supersedes_link_idempotent():
    """Second call with same (old_id, new_id) returns None (no-op)."""
    owner = await _make_owner()
    a = await _make_memory(owner, "Факт A", created_offset_days=20)
    b = await _make_memory(owner, "Факт B", created_offset_days=10)

    async with get_session() as session:
        link1 = await add_supersedes_link(
            session,
            owner.id,
            old_id=a.id,
            new_id=b.id,
        )
        link2 = await add_supersedes_link(
            session,
            owner.id,
            old_id=a.id,
            new_id=b.id,
        )
        await session.commit()

    assert link1 is not None
    assert link2 is None


@pytest.mark.asyncio
async def test_add_supersedes_link_same_id_returns_none():
    """old_id == new_id → returns None (no self-link)."""
    owner = await _make_owner()
    mem = await _make_memory(owner, "Факт ABC", created_offset_days=10)

    async with get_session() as session:
        link = await add_supersedes_link(
            session,
            owner.id,
            old_id=mem.id,
            new_id=mem.id,
        )
        await session.commit()

    assert link is None


# ═══════════════════════════════════════════════════════════════════
#  Tests: apply_reval_result
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_apply_reval_result_past_creates_supersedes():
    """action='past' → new fact created, old deactivated, supersedes link."""
    owner = await _make_owner()
    old = await _make_memory(
        owner,
        "Планирую поездку в Сингапур",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )
    parsed = {
        "action": "past",
        "updated_fact": "Съездил в Сингапур в июле 2026",
        "new_memory_type": "personal",
        "decay_rate": 0.03,
        "reason": "событие произошло",
    }

    async with get_session() as session:
        result = await apply_reval_result(session, owner, old, parsed)
        await session.commit()

    assert result.action == "past"
    assert result.new_memory_id is not None
    assert result.new_memory_id != old.id

    # Verify DB state
    async with get_session() as session:
        new_mem = await session.get(Memory, result.new_memory_id)
        assert new_mem is not None
        assert new_mem.fact == "Съездил в Сингапур в июле 2026"
        assert new_mem.memory_type == "personal"
        assert new_mem.source == "dreaming_reval"
        assert new_mem.is_active is True

        old_mem = await session.get(Memory, old.id)
        assert old_mem.is_active is False

        # Supersedes link exists
        from sqlalchemy import select

        link = (
            await session.execute(
                select(MemoryLink).where(
                    MemoryLink.user_id == owner.id,
                    MemoryLink.source_id == old.id,
                    MemoryLink.target_id == new_mem.id,
                    MemoryLink.relation_type == "supersedes",
                )
            )
        ).scalar_one_or_none()
        assert link is not None


@pytest.mark.asyncio
async def test_apply_reval_result_invalid_deactivates():
    """action='invalid' → is_active=False, no new fact."""
    owner = await _make_owner()
    mem = await _make_memory(
        owner,
        "Устаревший факт",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )
    parsed = {"action": "invalid", "reason": "больше не актуально"}

    async with get_session() as session:
        result = await apply_reval_result(session, owner, mem, parsed)
        await session.commit()

    assert result.action == "invalid"
    assert result.new_memory_id is None

    async with get_session() as session:
        old_mem = await session.get(Memory, mem.id)
        assert old_mem.is_active is False


@pytest.mark.asyncio
async def test_apply_reval_result_skip_no_change():
    """action='skip' → no changes to facts."""
    owner = await _make_owner()
    mem = await _make_memory(
        owner,
        "Актуальный факт",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )
    parsed = {"action": "skip", "reason": "всё ещё актуально"}

    async with get_session() as session:
        result = await apply_reval_result(session, owner, mem, parsed)
        await session.commit()

    assert result.action == "skip"
    assert result.new_memory_id is None

    async with get_session() as session:
        old_mem = await session.get(Memory, mem.id)
        assert old_mem.is_active is True
        assert old_mem.fact == "Актуальный факт"


@pytest.mark.asyncio
async def test_apply_reval_result_none_treats_as_skip():
    """parsed=None → treated as skip, no crash."""
    owner = await _make_owner()
    mem = await _make_memory(
        owner,
        "Факт",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )

    async with get_session() as session:
        result = await apply_reval_result(session, owner, mem, None)
        await session.commit()

    assert result.action == "skip"
    assert "no parseable" in result.reason.lower()

    async with get_session() as session:
        m = await session.get(Memory, mem.id)
        assert m.is_active is True


@pytest.mark.asyncio
async def test_apply_reval_result_permanent_creates_fact():
    """action='permanent' → new fact with low decay_rate, old deactivated."""
    owner = await _make_owner()
    old = await _make_memory(
        owner,
        "Важный временный факт",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )
    parsed = {
        "action": "permanent",
        "updated_fact": "Важный факт навсегда",
        "new_memory_type": "personal",
        "decay_rate": 0.01,
        "reason": "должен сохраниться",
    }

    async with get_session() as session:
        result = await apply_reval_result(session, owner, old, parsed)
        await session.commit()

    assert result.action == "permanent"
    assert result.new_memory_id is not None

    async with get_session() as session:
        new_mem = await session.get(Memory, result.new_memory_id)
        assert new_mem.source == "dreaming_reval"
        assert new_mem.decay_rate == 0.01
        assert new_mem.memory_type == "personal"
        assert new_mem.is_active is True

        old_mem = await session.get(Memory, old.id)
        assert old_mem.is_active is False


# ═══════════════════════════════════════════════════════════════════
#  Tests: rollback_recent_revals
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rollback_recent_revals_restores():
    """Rollback reactivates old facts, deactivates new ones, removes links."""
    owner = await _make_owner()
    old = await _make_memory(
        owner,
        "Старый факт",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )
    parsed = {
        "action": "past",
        "updated_fact": "Новый факт после переоценки",
        "new_memory_type": "personal",
        "decay_rate": 0.03,
        "reason": "test rollback",
    }

    async with get_session() as session:
        result = await apply_reval_result(session, owner, old, parsed)
        await session.commit()

    assert result.new_memory_id is not None
    new_id = result.new_memory_id

    # Perform rollback
    undone = await rollback_recent_revals(owner.telegram_id, limit=10)
    assert undone >= 1

    async with get_session() as session:
        # Old fact is active again
        old_mem = await session.get(Memory, old.id)
        assert old_mem.is_active is True

        # New fact is deactivated
        new_mem = await session.get(Memory, new_id)
        assert new_mem.is_active is False

        # Link is gone
        from sqlalchemy import select

        link = (
            await session.execute(
                select(MemoryLink).where(
                    MemoryLink.user_id == owner.id,
                    MemoryLink.source_id == old.id,
                    MemoryLink.target_id == new_id,
                    MemoryLink.relation_type == "supersedes",
                )
            )
        ).scalar_one_or_none()
        assert link is None


@pytest.mark.asyncio
async def test_rollback_recent_revals_no_facts_returns_zero():
    """Rollback with no dreaming_reval facts returns 0."""
    owner = await _make_owner()
    undone = await rollback_recent_revals(owner.telegram_id, limit=10)
    assert undone == 0


# ═══════════════════════════════════════════════════════════════════
#  Tests: _build_user_prompt
# ═══════════════════════════════════════════════════════════════════


def test_build_user_prompt_contains_key_fields():
    """Prompt includes today, created_at, memory_type, fact text."""
    fact = Memory(
        id=1,
        user_id=1,
        fact="Тестовый факт",
        memory_type="temporary",
        importance=0.8,
        created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        validity_end=datetime(2026, 7, 15, tzinfo=timezone.utc),
        expires_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    today = datetime(2026, 6, 5, tzinfo=timezone.utc)

    prompt = _build_user_prompt(fact, today)

    assert "2026-06-05" in prompt  # today
    assert "2026-01-15" in prompt  # created_at
    assert "2026-07-15" in prompt  # validity_end
    assert "2026-08-15" in prompt  # expires_at
    assert "temporary" in prompt  # memory_type
    assert "0.80" in prompt  # importance
    assert "Тестовый факт" in prompt  # fact text


def test_build_user_prompt_null_fields():
    """Prompt handles None for validity_end, expires_at, importance."""
    fact = Memory(
        id=2,
        user_id=1,
        fact="Факт без дат",
        memory_type="task",
        importance=None,
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        validity_end=None,
        expires_at=None,
    )
    today = datetime(2026, 6, 5, tzinfo=timezone.utc)

    prompt = _build_user_prompt(fact, today)
    # Should not contain importance line (None → filtered out)
    assert "importance" not in prompt
    assert "не указана" in prompt  # validity_end null text
    assert "не указан" in prompt  # expires_at null text


# ═══════════════════════════════════════════════════════════════════
#  Tests: revaluate_fact (LLM call + parsing)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_revaluate_fact_with_mock_provider():
    """revaluate_fact calls provider.chat and parses response."""
    owner = await _make_owner()
    mem = await _make_memory(
        owner,
        "Планирую поездку в июле",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )

    mock = MockLLMProvider(
        [
            json.dumps(
                {
                    "action": "past",
                    "updated_fact": "Съездил в июле 2026",
                    "new_memory_type": "personal",
                    "decay_rate": 0.03,
                    "reason": "событие произошло",
                }
            )
        ]
    )

    result = await reval_fact(mock, mem)
    assert result is not None
    assert result["action"] == "past"
    assert result["updated_fact"] == "Съездил в июле 2026"
    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_revaluate_fact_error_returns_none():
    """revaluate_fact returns None when provider raises, no exception propagated."""
    owner = await _make_owner()
    mem = await _make_memory(
        owner,
        "Факт",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )

    class ErrorProvider:
        name = "error"

        async def chat(self, messages, *, heavy=False, task_type="default"):
            raise RuntimeError("simulated provider failure")

        async def close(self):
            pass

    result = await reval_fact(ErrorProvider(), mem)
    assert result is None


# ═══════════════════════════════════════════════════════════════════
#  Tests: revaluation_summary_text
# ═══════════════════════════════════════════════════════════════════


def test_revaluation_summary_text_empty():
    """Summary with examined=0 returns 'нет устаревших фактов'."""
    summary = RevalBatchSummary(examined=0)
    text = reval_summary_text(summary)
    assert "нет устаревших фактов" in text


def test_revaluation_summary_text_with_results():
    """Summary includes counts for each action."""
    summary = RevalBatchSummary(
        examined=10,
        past=3,
        permanent=1,
        invalid=2,
        skip=4,
        errors=0,
    )
    text = reval_summary_text(summary)
    assert "Проверено: 10" in text
    assert "Произошло" in text
    assert "Сделано постоянным" in text
    assert "Деактивировано" in text
    assert "Без изменений" in text


def test_revaluation_summary_text_with_errors():
    """Summary shows error count when present."""
    summary = RevalBatchSummary(examined=5, errors=2)
    text = reval_summary_text(summary)
    assert "Ошибок: 2" in text


# ═══════════════════════════════════════════════════════════════════
#  Tests: revaluation_run (end-to-end with mock)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_reval_run_disabled_in_settings():
    """reval_run returns empty summary when dreaming_reval_enabled=False."""
    from src.config import settings

    owner = await _make_owner()
    await _make_memory(
        owner,
        "Старый факт",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )

    original = settings.dreaming_reval_enabled
    try:
        settings.dreaming_reval_enabled = False
        result = await reval_run(owner.telegram_id, limit=5)
    finally:
        settings.dreaming_reval_enabled = original

    assert result.examined == 0


@pytest.mark.asyncio
async def test_reval_run_with_mock_provider():
    """End-to-end run with MockLLMProvider processes facts and creates results."""
    from src.config import settings

    owner = await _make_owner()
    # Create 2 stale facts
    await _make_memory(
        owner,
        "Планирую позвонить маме",
        memory_type="temporary",
        confidence=0.9,
        created_offset_days=10,
    )
    await _make_memory(
        owner,
        "Хочу купить новый ноутбук",
        memory_type="temporary",
        confidence=0.8,
        created_offset_days=15,
    )

    mock_provider = MockLLMProvider(
        [
            json.dumps(
                {
                    "action": "past",
                    "updated_fact": "Позвонил маме вчера",
                    "new_memory_type": "personal",
                    "decay_rate": 0.03,
                    "reason": "звонок состоялся",
                }
            ),
            json.dumps(
                {
                    "action": "skip",
                    "reason": "всё ещё планирует",
                }
            ),
        ]
    )

    # Patch build_provider to return our mock
    with patch(
        "src.llm.router.build_provider",
        new=AsyncMock(return_value=mock_provider),
    ):
        summary = await reval_run(owner.telegram_id, limit=5)

    assert summary.examined == 2
    assert summary.past >= 1
    assert summary.skip >= 1
    assert summary.errors == 0
    assert len(summary.results) == 2

    # One new fact should have been created
    past_results = [r for r in summary.results if r.action == "past"]
    assert len(past_results) == 1
    assert past_results[0].new_memory_id is not None

    # Verify DB state
    async with get_session() as session:
        from sqlalchemy import select

        # New fact exists with source='dreaming_reval'
        new_facts = (
            (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == owner.id,
                        Memory.source == "dreaming_reval",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(new_facts) == 1
        assert new_facts[0].fact == "Позвонил маме вчера"
        assert new_facts[0].is_active is True

        # Supersedes link exists
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
        assert len(links) == 1
