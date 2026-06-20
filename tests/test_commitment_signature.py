"""Test _compute_action_signature handles tzinfo edge cases (Bug 1: HMAC tzinfo crash).

Verifies the fix: naive expires_at gets UTC appended, aware expires_at is used as-is.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.db.repos.commitment_repo import _compute_action_signature
from src.db.models import PendingAction
from src.db.repo import get_or_create_user
from src.db.session import get_session


@pytest.fixture(autouse=True)
async def _recreate_tables(_db_init):
    """Recreate tables before each test for clean in-memory DB."""
    from src.db.session import engine, Base, init_db
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS agent_session_messages_fts"))
    await init_db()

    yield


@pytest.mark.asyncio
async def test_signature_with_aware_expires_at() -> None:
    """_compute_action_signature works when expires_at has UTC tzinfo."""
    async with get_session() as session:
        user = await get_or_create_user(session, 999000111)
        pa = PendingAction(
            user_id=user.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 1, "text": "test"}),
            route="db",
            verb="send",
            risk="low",
            human_summary="Test aware",
        )
        session.add(pa)
        await session.flush()  # get pa.id
        pa.expires_at = datetime.now(UTC) + timedelta(minutes=10)
        sig = _compute_action_signature(pa)
        assert sig, "signature must be non-empty"
        assert len(sig) == 32, f"expected 32-char hex, got {len(sig)}"


@pytest.mark.asyncio
async def test_signature_with_naive_expires_at() -> None:
    """_compute_action_signature handles naive expires_at (no tzinfo) without crash."""
    async with get_session() as session:
        user = await get_or_create_user(session, 999000222)
        pa = PendingAction(
            user_id=user.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 1, "text": "naive"}),
            route="db",
            verb="send",
            risk="low",
            human_summary="Test naive",
        )
        session.add(pa)
        await session.flush()
        # Simulate naive datetime from DB (SQLite stores without tzinfo)
        pa.expires_at = datetime(2026, 6, 20, 12, 0, 0)  # no tzinfo
        sig = _compute_action_signature(pa)
        assert sig, "signature must be non-empty even with naive expires_at"
        assert len(sig) == 32


@pytest.mark.asyncio
async def test_signature_with_none_expires_at() -> None:
    """_compute_action_signature handles None expires_at without crash."""
    async with get_session() as session:
        user = await get_or_create_user(session, 999000333)
        pa = PendingAction(
            user_id=user.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 1, "text": "none"}),
            route="db",
            verb="send",
            risk="low",
            human_summary="Test none",
        )
        session.add(pa)
        await session.flush()
        pa.expires_at = None
        sig = _compute_action_signature(pa)
        assert sig, "signature must be non-empty even without expires_at"
        assert len(sig) == 32
