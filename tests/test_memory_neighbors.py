"""Tests for memory_neighbors module.

Focus: find_cross_contact_bridges uses batch embedding instead of N+1 embed calls.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")

from src.core.memory.memory_neighbors import find_cross_contact_bridges
from src.db.repo import get_or_create_user
from src.core.memory.memory_service import save_memory_single
from src.db.session import get_session

OWNER_TG_ID = 123456789


@pytest.fixture(autouse=True)
def setup_db():
    """Recreate all tables before each test."""
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


async def _seed_memories() -> None:
    """Create owner with 12 active memories across 3 contacts."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        for i in range(12):
            await save_memory_single(
                session,
                owner,
                fact=f"fact about topic {i % 3} number {i}",
                contact_id=1000 + (i % 3),
                source="chat",
                confidence=0.5,
                memory_type=None,
            )


async def test_find_cross_contact_bridges_uses_batch_embedding():
    """Batch-embeds all candidate facts with a single embed_batch call."""
    await _seed_memories()

    provider = AsyncMock()
    provider.embed_batch = AsyncMock(return_value=[[0.0] * 128 for _ in range(12)])
    provider.embed = AsyncMock(return_value=[0.0] * 128)

    vector_store = AsyncMock()
    # Each search returns a neighbor from a different contact with high similarity.
    vector_store.search_similar_memories = AsyncMock(
        side_effect=lambda **kwargs: [
            {
                "memory_id": 99999,
                "contact_id": 1002,
                "fact": "bridge fact",
                "score": 0.9,
            }
        ]
    )

    fake_contact = MagicMock()
    fake_contact.display_name = "Test Contact"

    with (
        patch(
            "src.core.memory.memory_neighbors.build_provider",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "src.core.actions.vector_store.get_vector_store",
            new_callable=AsyncMock,
        ) as mock_get_vector_store,
        patch(
            "src.core.memory.memory_neighbors.get_contact", new_callable=AsyncMock
        ) as mock_get_contact,
    ):
        mock_get_vector_store.return_value = vector_store
        mock_get_contact.return_value = fake_contact
        bridges = await find_cross_contact_bridges(OWNER_TG_ID)

    provider.embed_batch.assert_awaited_once()
    provider.embed.assert_not_awaited()
    # 12 active facts are batched into one search batch; search is called once per fact.
    assert vector_store.search_similar_memories.await_count == 12
    # At least one bridge found, and the function caps at 5 bridges.
    assert 0 < len(bridges) <= 5
    assert bridges[0]["contact1"] == "Test Contact"
    assert bridges[0]["similarity"] == 0.9


async def test_find_cross_contact_bridges_no_provider():
    """Returns empty list when provider is not available."""
    await _seed_memories()
    with patch(
        "src.core.memory.memory_neighbors.build_provider",
        new_callable=AsyncMock,
        return_value=None,
    ):
        bridges = await find_cross_contact_bridges(OWNER_TG_ID)
    assert bridges == []


async def test_find_cross_contact_bridges_embed_batch_failure():
    """Returns empty list when batch embedding fails."""
    await _seed_memories()

    provider = AsyncMock()
    provider.embed_batch = AsyncMock(side_effect=RuntimeError("embedding failed"))

    with patch(
        "src.core.memory.memory_neighbors.build_provider",
        new_callable=AsyncMock,
        return_value=provider,
    ):
        bridges = await find_cross_contact_bridges(OWNER_TG_ID)

    assert bridges == []
