"""Tests for hybrid vector+FTS5 memory agent search (Phase 2.4)."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ.setdefault("BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef")
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.db.session import init_db, get_session
from src.db.repo import get_or_create_user
from src.core.memory.memory_service import save_memory_single

OWNER_TG_ID = 123456789


@pytest.fixture(autouse=True)
async def setup_db():
    """Fresh in-memory DB per test."""
    from src.db.session import engine, Base
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
    await init_db()
    yield
    engine.sync_engine.dispose()


async def _seed_facts(session, owner, facts: list[str]):
    """Insert facts and return owner."""
    for fact in facts:
        await save_memory_single(session, owner, fact=fact,
            source="chat",
            confidence=0.5,
            memory_type=None)


# ---------------------------------------------------------------------------
# hybrid_memory_facts tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fts5_fallback_when_no_vector_store():
    """When vector store is unavailable, falls back to FTS5-only."""
    from src.core.intelligence.agent_dispatcher import _hybrid_memory_facts

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        await _seed_facts(
            session,
            owner,
            [
                "Alice любит кофе",
                "Bob предпочитает чай",
                "Charlie работает в IT",
            ],
        )
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        facts = await _hybrid_memory_facts(session, owner, "кофе", limit=5)

    assert len(facts) >= 1
    assert any("кофе" in f for f in facts)


@pytest.mark.asyncio
async def test_fts5_returns_no_results_for_unknown_query():
    """Empty result for query with no matches."""
    from src.core.intelligence.agent_dispatcher import _hybrid_memory_facts

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        await _seed_facts(session, owner, ["Alice любит кофе"])

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        facts = await _hybrid_memory_facts(session, owner, "zzz_nonexistent", limit=5)

    assert facts == []


@pytest.mark.asyncio
async def test_scored_fact_format():
    """Facts include relevance score annotation."""
    from src.core.intelligence.agent_dispatcher import _hybrid_memory_facts

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        await _seed_facts(
            session,
            owner,
            ["Alice любит зелёный чай с жасмином по утрам"],
        )

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        facts = await _hybrid_memory_facts(session, owner, "чай", limit=5)

    assert len(facts) >= 1
    # Should have score annotation from FTS5-only fallback
    assert "(score:" in facts[0]


# ---------------------------------------------------------------------------
# recall() with scored facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_with_scored_facts():
    """recall() accepts scored fact strings and uses them."""
    from src.agents.memory_agent import recall

    mock_provider = AsyncMock()
    mock_provider.chat.return_value = '{"answer": "Alice любит чай", "relevant_facts": ["Alice любит чай (score: 95%)"]}'

    facts = [
        "Alice любит чай (score: 95%)",
        "Bob пьёт кофе (score: 45%)",
    ]
    result = await recall(mock_provider, "Кто любит чай?", facts)

    assert result["answer"] == "Alice любит чай"
    assert len(result["relevant_facts"]) >= 1


@pytest.mark.asyncio
async def test_recall_no_facts():
    """recall() returns empty answer when no facts available."""
    from src.agents.memory_agent import recall

    mock_provider = AsyncMock()
    result = await recall(mock_provider, "Кто любит чай?", [])

    assert result["answer"] == "Нет сохранённых фактов."
    assert result["relevant_facts"] == []


# ---------------------------------------------------------------------------
# RRF fusion unit test
# ---------------------------------------------------------------------------


def test_reciprocal_rank_fusion_combines_rankings():
    """RRF merges vector and keyword results."""
    from src.core.memory.hybrid_search import reciprocal_rank_fusion

    vector = [(1, 0.95), (2, 0.80)]
    keyword = [(2, 0.10), (3, 0.08)]

    fused = reciprocal_rank_fusion(
        vector_results=vector,
        keyword_results=keyword,
    )

    assert len(fused) >= 2
    # ID 2 should rank high (appears in both lists)
    fused_ids = [mid for mid, _ in fused]
    assert 2 in fused_ids


def test_reciprocal_rank_fusion_empty_inputs():
    """RRF handles None/empty inputs gracefully."""
    from src.core.memory.hybrid_search import reciprocal_rank_fusion

    # All None
    assert reciprocal_rank_fusion() == []

    # Only keyword
    keyword = [(1, 0.5)]
    fused = reciprocal_rank_fusion(keyword_results=keyword)
    assert len(fused) == 1
    assert fused[0][0] == 1


# ---------------------------------------------------------------------------
# Import smoke test
# ---------------------------------------------------------------------------


def test_hybrid_memory_facts_importable():
    """_hybrid_memory_facts is importable from agent_dispatcher."""
    from src.core.intelligence.agent_dispatcher import _hybrid_memory_facts

    assert callable(_hybrid_memory_facts)
