"""Tests for skills_curator lifecycle management."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"  # noqa: S105

from src.core.intelligence.skills_curator import (
    archive_long_disabled,
    curator_stats,
    decay_stale_skills,
)
from src.db.models import Skill
from src.db.repo import get_or_create_user
from src.db.session import get_session

OWNER_TG_ID = 123456789


@pytest.fixture(autouse=True)
async def setup_db():
    """Recreate tables before each test."""
    from src.db.session import Base, engine, init_db
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
    await init_db()

    yield

    engine.sync_engine.dispose()


async def _make_skill(
    session,
    *,
    name: str,
    enabled: bool = True,
    disabled_at: datetime | None = None,
    review_status: str = "approved",
    success_count: int = 0,
    failure_count: int = 0,
    last_used_at: datetime | None = None,
    body: str = "test body",
) -> Skill:
    user = await get_or_create_user(session, OWNER_TG_ID)
    skill = Skill(
        user_id=user.id,
        name=name,
        body=body,
        enabled=enabled,
        disabled_at=disabled_at,
        review_status=review_status,
        success_count=success_count,
        failure_count=failure_count,
        last_used_at=last_used_at,
    )
    session.add(skill)
    await session.flush()
    return skill


async def test_decay_stale_skills_disables_and_records_disabled_at() -> None:
    async with get_session() as session:
        now = datetime.now(UTC)
        skill = await _make_skill(
            session,
            name="stale-fail",
            enabled=True,
            success_count=2,
            failure_count=8,
            last_used_at=now - timedelta(days=40),
        )

        decayed = await decay_stale_skills(session, OWNER_TG_ID)
        await session.refresh(skill)

    assert decayed == 1
    assert skill.enabled is False
    assert skill.disabled_at is not None
    assert "DECAYED" in skill.description


async def test_decay_ignores_high_success_rate_skills() -> None:
    async with get_session() as session:
        await _make_skill(
            session,
            name="popular-skill",
            enabled=True,
            success_count=8,
            failure_count=2,
        )

        decayed = await decay_stale_skills(session, OWNER_TG_ID)

    assert decayed == 0


async def test_archive_long_disabled_skips_fresh_disabled() -> None:
    async with get_session() as session:
        now = datetime.now(UTC)
        await _make_skill(
            session,
            name="fresh-disabled",
            enabled=False,
            disabled_at=now - timedelta(days=30),
        )

        archived = await archive_long_disabled(session, OWNER_TG_ID, days=90)

    assert archived == 0


async def test_archive_long_disabled_archives_old_disabled() -> None:
    async with get_session() as session:
        now = datetime.now(UTC)
        skill = await _make_skill(
            session,
            name="old-disabled",
            enabled=False,
            disabled_at=now - timedelta(days=100),
        )

        archived = await archive_long_disabled(session, OWNER_TG_ID, days=90)
        await session.refresh(skill)

    assert archived == 1
    assert skill.review_status == "archived"
    assert "ARCHIVED" in skill.description


async def test_archive_long_disabled_skips_already_archived() -> None:
    async with get_session() as session:
        now = datetime.now(UTC)
        await _make_skill(
            session,
            name="already-archived",
            enabled=False,
            disabled_at=now - timedelta(days=100),
            review_status="archived",
        )

        archived = await archive_long_disabled(session, OWNER_TG_ID, days=90)

    assert archived == 0


async def test_archive_long_disabled_skips_enabled_skills() -> None:
    async with get_session() as session:
        now = datetime.now(UTC)
        await _make_skill(
            session,
            name="still-enabled",
            enabled=True,
            disabled_at=now - timedelta(days=100),
        )

        archived = await archive_long_disabled(session, OWNER_TG_ID, days=90)

    assert archived == 0


async def test_curator_stats_returns_counts() -> None:
    async with get_session() as session:
        await _make_skill(session, name="proposed-skill", review_status="proposed")
        await _make_skill(session, name="approved-skill", review_status="approved")
        await _make_skill(session, name="rejected-skill", review_status="rejected")

    stats = await curator_stats(OWNER_TG_ID)

    assert stats["proposed"] == 1
    assert stats["approved"] == 1
    assert stats["rejected"] == 1
    assert stats["total"] == 3
