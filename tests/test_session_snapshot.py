"""Tests for bounded session snapshot (Week 5).

Covers:
  - build_session_snapshot combines facts, session summary, contact digest,
    pending questions, style, risk hints.
  - format_snapshot produces a compact prompt block.
  - token budget trimming keeps the snapshot bounded.
  - peek_pending does not drain the in-memory queue.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"  # noqa: S105

from src.core.context.token_tracker import estimate_tokens
from src.core.memory.pending_questions import (
    add_question,
    delete_pending_questions,
    peek_pending,
)
from src.core.memory.session_snapshot import (
    _trim_facts_to_budget,
    build_session_snapshot,
    format_snapshot,
)


@pytest.fixture
async def _reset_pending():
    """Clear the in-memory pending queue before each test."""
    await delete_pending_questions(1)
    await delete_pending_questions(7)
    yield
    await delete_pending_questions(1)
    await delete_pending_questions(7)


def _make_fact(fact: str, reason: str = "recall"):
    f = MagicMock()
    f.fact = fact
    f.reason = reason
    return f


@pytest.mark.asyncio
async def test_build_snapshot_basic_facts():
    """Snapshot includes facts from recall."""
    with (
        patch(
            "src.core.memory.session_snapshot.recall",
            AsyncMock(
                return_value=MagicMock(
                    facts=[
                        _make_fact("Fact one"),
                        _make_fact("Fact two"),
                    ]
                )
            ),
        ),
        patch(
            "src.core.memory.session_context.load_session_context",
            AsyncMock(return_value=None),
        ),
    ):
        snap = await build_session_snapshot(telegram_id=1, user_text="hello")

    assert snap["facts"] == ["Fact one", "Fact two"]
    assert snap["contact_digest"] is None
    assert snap["token_estimate"] > 0


@pytest.mark.asyncio
async def test_build_snapshot_session_summary():
    """Snapshot includes session summary and active tasks."""
    with (
        patch(
            "src.core.memory.session_snapshot.recall",
            AsyncMock(return_value=MagicMock(facts=[])),
        ),
        patch(
            "src.core.memory.session_context.load_session_context",
            AsyncMock(
                return_value={
                    "context_summary": "We talked about travel",
                    "active_tasks": '["book flight"]',
                }
            ),
        ),
    ):
        snap = await build_session_snapshot(telegram_id=1)

    assert snap["session_summary"] == "We talked about travel"
    assert snap["active_tasks"] == '["book flight"]'


@pytest.mark.asyncio
async def test_build_snapshot_contact_digest():
    """Snapshot includes contact digest, style, and risk hints."""
    digest = {
        "display_name": "Alice",
        "style": {
            "closeness": "close",
            "archetype": "friend",
            "directness": "high",
            "tone": "warm",
        },
        "promises": [{"text": "call back"}],
        "risks": [{"type": "low_health"}],
        "facts": [],
    }
    with (
        patch(
            "src.core.memory.session_snapshot.recall",
            AsyncMock(return_value=MagicMock(facts=[])),
        ),
        patch(
            "src.core.memory.session_context.load_session_context",
            AsyncMock(return_value=None),
        ),
        patch(
            "src.core.contacts.contact_memory_digest.get_contact_digest",
            AsyncMock(return_value=digest),
        ),
    ):
        snap = await build_session_snapshot(telegram_id=1, contact_id=42)

    assert snap["contact_digest"] == digest
    assert "close" in snap["style"]
    assert "low_health" in snap["risk_hints"]


@pytest.mark.asyncio
async def test_build_snapshot_pending_questions(_reset_pending):
    """Snapshot includes pending questions without draining them."""
    await add_question(1, "Q1")
    await add_question(1, "Q2")

    with (
        patch(
            "src.core.memory.session_snapshot.recall",
            AsyncMock(return_value=MagicMock(facts=[])),
        ),
        patch(
            "src.core.memory.session_context.load_session_context",
            AsyncMock(return_value=None),
        ),
    ):
        snap = await build_session_snapshot(telegram_id=1)

    assert snap["pending_questions"] == ["Q1", "Q2"]
    # Queue still there
    remaining = await peek_pending(1)
    assert len(remaining) == 2


@pytest.mark.asyncio
async def test_format_snapshot_full():
    """format_snapshot renders all populated sections."""
    snap = {
        "facts": ["Fact A"],
        "contact_digest": {
            "display_name": "Bob",
            "promises": [{"text": "send report"}],
            "risks": [],
        },
        "pending_questions": ["What time?"],
        "style": "closeness=work",
        "risk_hints": [],
        "session_summary": "Discussed project",
        "active_tasks": "",
        "token_estimate": 0,
    }
    text = format_snapshot(snap)
    assert "Discussed project" in text
    assert "Fact A" in text
    assert "Bob" in text
    assert "send report" in text
    assert "What time?" in text


@pytest.mark.asyncio
async def test_format_snapshot_empty():
    """format_snapshot returns empty string for empty snapshot."""
    assert format_snapshot({}) == ""
    assert format_snapshot(None) == ""  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_trim_facts_to_budget():
    """Long facts are trimmed to fit the token budget."""
    long_facts = ["word " * 100 for _ in range(7)]
    snap = {
        "facts": list(long_facts),
        "contact_digest": None,
        "pending_questions": [],
        "style": "",
        "risk_hints": [],
        "session_summary": "",
        "active_tasks": "",
        "token_estimate": 0,
    }
    _trim_facts_to_budget(snap)
    assert len(snap["facts"]) < 7
    assert estimate_tokens(format_snapshot(snap)) <= 512


@pytest.mark.asyncio
async def test_build_snapshot_graceful_failure():
    """Snapshot returns empty structure if all subsystems fail."""
    with (
        patch(
            "src.core.memory.session_snapshot.recall",
            AsyncMock(side_effect=RuntimeError("recall boom")),
        ),
        patch(
            "src.core.memory.session_context.load_session_context",
            AsyncMock(side_effect=RuntimeError("session boom")),
        ),
    ):
        snap = await build_session_snapshot(telegram_id=1)

    assert snap["facts"] == []
    assert snap["session_summary"] == ""
    assert snap["contact_digest"] is None
    assert snap["token_estimate"] == 0


@pytest.mark.asyncio
async def test_peek_pending_does_not_drain():
    """peek_pending returns questions without removing them."""
    await add_question(7, "Q")
    first = await peek_pending(7)
    second = await peek_pending(7)
    assert first == second
    assert len(first) == 1


def test_estimate_tokens_in_module():
    """estimate_tokens is imported and used for token_estimate."""
    assert estimate_tokens("hello world") > 0
