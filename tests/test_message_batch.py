"""Tests for message_repo batch query functions.

Verifies fetch_latest_message_per_contact and fetch_latest_messages_per_contact
using in-memory SQLite via the _db_init fixture.
"""

from __future__ import annotations

from datetime import UTC as datetime_utc
from datetime import datetime, timedelta

import pytest

from src.db.models import Message
from src.db.repo import (
    fetch_latest_message_per_contact,
    fetch_latest_messages_per_contact,
    get_or_create_user,
)
from src.db.session import get_session

# Unique telegram_id avoids collisions with other test files.
_TEST_TG_ID = 987654321


@pytest.fixture(autouse=True)
async def _recreate_tables(_db_init):
    """Recreate all tables before each test for a clean in-memory DB."""
    from sqlalchemy import text
    from src.db.session import (
        Base,
        _FTS_SETUP,
        _MEMORY_FTS_SETUP,
        _SESSION_FTS_SETUP,
        engine,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for tbl in ("messages_fts", "memories_fts", "agent_session_messages_fts"):
            await conn.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _FTS_SETUP:
            await conn.execute(text(stmt))
        for stmt in _SESSION_FTS_SETUP:
            await conn.execute(text(stmt))
        for stmt in _MEMORY_FTS_SETUP:
            await conn.execute(text(stmt))

    yield


async def _seed(
    session, user, peer_msgs: dict[int, list[tuple[int, int, str]]]
) -> None:
    """Insert test messages. peer_msgs maps peer_id → [(msg_id, hours_ago, text), ...].

    Commit is handled by the enclosing get_session() context manager.
    """
    now = datetime.now(datetime_utc)
    msgs = [
        Message(
            user_id=user.id,
            peer_id=pid,
            message_id=mid,
            date=now - timedelta(hours=h),
            kind="text",
            text=text,
        )
        for pid, entries in peer_msgs.items()
        for mid, h, text in entries
    ]
    session.add_all(msgs)


# ═════════════════════════════════════════════════════════════════════════
#  fetch_latest_message_per_contact
# ═════════════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("_db_init")
@pytest.mark.asyncio
async def test_fetch_latest_message_per_contact() -> None:
    """Returns exactly the newest message per peer_id (one row per peer)."""
    async with get_session() as session:
        user = await get_or_create_user(session, _TEST_TG_ID)
        await _seed(
            session,
            user,
            {
                101: [(1, 3, "p101-old"), (2, 1, "p101-new")],
                102: [(1, 2, "p102-only")],
            },
        )

    async with get_session() as session:
        # Normal: one latest per peer
        result = await fetch_latest_message_per_contact(session, user, [101, 102])
        assert len(result) == 2
        assert result[101].text == "p101-new"
        assert result[102].text == "p102-only"
        # Edge: empty input → empty dict
        assert await fetch_latest_message_per_contact(session, user, []) == {}
        # Edge: unknown peer → empty dict
        assert await fetch_latest_message_per_contact(session, user, [999]) == {}


# ═════════════════════════════════════════════════════════════════════════
#  fetch_latest_messages_per_contact
# ═════════════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("_db_init")
@pytest.mark.asyncio
async def test_fetch_latest_messages_per_contact() -> None:
    """Returns up to `limit` newest messages per peer, reverse chronological."""
    async with get_session() as session:
        user = await get_or_create_user(session, _TEST_TG_ID)
        await _seed(
            session,
            user,
            {
                201: [
                    (1, 4, "p201-oldest"),
                    (2, 3, "p201-old"),
                    (3, 2, "p201-mid"),
                    (4, 1, "p201-newest"),
                ],
                202: [
                    (1, 5, "p202-older"),
                    (2, 1, "p202-newest"),
                ],
            },
        )

    async with get_session() as session:
        result = await fetch_latest_messages_per_contact(
            session, user, [201, 202], limit=3
        )
        # peer 201 has 4 msgs, limit 3 -> 3 newest, newest first
        texts_201 = [m.text for m in result[201]]
        assert texts_201 == ["p201-newest", "p201-mid", "p201-old"]
        dates_201 = [m.date for m in result[201]]
        assert dates_201 == sorted(dates_201, reverse=True)
        assert len(result[201]) == 3
        # peer 202 has 2 msgs, limit 3 -> all 2 in reverse chronological
        texts_202 = [m.text for m in result[202]]
        assert texts_202 == ["p202-newest", "p202-older"]
        dates_202 = [m.date for m in result[202]]
        assert dates_202 == sorted(dates_202, reverse=True)
        assert len(result[202]) == 2
        # Edge: empty input -> empty dict
        assert await fetch_latest_messages_per_contact(session, user, [], limit=3) == {}
