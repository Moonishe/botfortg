"""Тесты для unified MemoryRecallService."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest
from datetime import datetime, timedelta, timezone

from src.db.session import get_session
from src.db.repo import get_or_create_user, add_commitment
from src.core.memory.memory_service import save_memory_single
from src.core.memory.memory_recall import (
    recall,
    format_recall_for_prompt,
    _mmr_rerank,
    _jaccard_similarity,
    RecalledFact,
    RecallResult,
)
from src.core.memory.hybrid_search import reciprocal_rank_fusion


@pytest.fixture(autouse=True)
async def setup_db():
    """Пересоздаёт таблицы перед каждым тестом."""
    from src.db.session import engine, Base, init_db
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
    await init_db()

    yield

    # Dispose pool so next test gets a fresh :memory: connection
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _fast_bump_debounce(monkeypatch):
    """Отключаем debounce use_count-бампера и чистим очередь для изоляции тестов."""
    from src.core.memory.memory_recall import _bump_queue

    _bump_queue.clear()
    monkeypatch.setattr("src.core.memory.memory_recall._BUMP_DEBOUNCE", 0.0)


def utc_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_pinned_above_normal():
    """Pinned факты всегда первые, даже с низким confidence."""
    async with get_session() as session:
        owner = await get_or_create_user(session, 123456)
        # pinned с низким confidence
        await save_memory_single(
            session, owner, fact="закреплённый факт", pinned=True, confidence=0.2,
            source="chat",
            memory_type=None)
        # обычный с высоким confidence
        await save_memory_single(
            session, owner, fact="обычный важный факт", pinned=False, confidence=0.95,
            source="chat",
            memory_type=None)
        await session.commit()

    result = await recall(123456, limit=5)
    assert len(result.facts) >= 2
    assert result.facts[0].fact == "закреплённый факт"
    assert "📌" in result.facts[0].reason


@pytest.mark.asyncio
async def test_task_priority():
    """Факты с memory_type=task и активным commitment попадают в результат."""
    async with get_session() as session:
        owner = await get_or_create_user(session, 123457)
        mem = await save_memory_single(session, owner, fact="сделать отчёт", memory_type="task",
            source="chat",
            confidence=0.5)
        await session.flush()
        await add_commitment(
            session,
            user_id=owner.id,
            peer_id=0,
            peer_name=None,
            message_id=None,
            direction="mine",
            text="сделать отчёт",
            deadline_at=None,
            source_memory_id=mem.id,
        )
        await session.commit()

    result = await recall(123457, include_tasks=True, limit=5)
    assert any("📋" in f.reason for f in result.facts), (
        "task-факт должен быть с reason «активная задача»"
    )


@pytest.mark.asyncio
async def test_expires_at_excludes():
    """Истёкшие факты не попадают в recall."""
    async with get_session() as session:
        owner = await get_or_create_user(session, 123458)
        past = utc_naive() - timedelta(hours=1)
        await save_memory_single(session, owner, fact="просроченный факт", expires_at=past,
            source="chat",
            confidence=0.5,
            memory_type=None)
        await save_memory_single(session, owner, fact="живой факт",
            source="chat",
            confidence=0.5,
            memory_type=None)
        await session.commit()

    result = await recall(123458, limit=5)
    facts_text = [f.fact for f in result.facts]
    assert "просроченный факт" not in facts_text
    assert "живой факт" in facts_text


@pytest.mark.asyncio
async def test_use_count_increments():
    """use_count растёт после каждого recall."""
    import asyncio as _aio

    async with get_session() as session:
        owner = await get_or_create_user(session, 123459)
        await save_memory_single(session, owner, fact="тестовый факт", confidence=0.8,
            source="chat",
            memory_type=None)
        await session.commit()

    # первый вызов
    r1 = await recall(123459, limit=5)
    assert len(r1.facts) >= 1
    mid = r1.facts[0].memory_id

    # проверяем use_count после первого вызова
    async with get_session() as session:
        from src.db.models import Memory
        from sqlalchemy import select

        m = (await session.execute(select(Memory).where(Memory.id == mid))).scalar_one()
        assert m.use_count >= 1

    # второй вызов (может вернуть из кеша + async bump)
    await recall(123459, limit=5)
    # Poll until fire-and-forget async bumper completes (robust, no sleep flake)
    from src.db.models import Memory as _M
    from sqlalchemy import select as _sel

    for _retry in range(10):  # up to 2 seconds total
        await _aio.sleep(0.2)
        async with get_session() as session:
            m = (await session.execute(_sel(_M).where(_M.id == mid))).scalar_one()
            if m.use_count >= 2:
                break
    assert m.use_count >= 2, (
        f"use_count should be >=2 after recall+retries, got {m.use_count}"
    )


@pytest.mark.asyncio
async def test_self_vs_contact_facts():
    """Self и contact факты корректно разделяются."""
    async with get_session() as session:
        owner = await get_or_create_user(session, 123460)
        await save_memory_single(session, owner, fact="я люблю кофе", contact_id=None,
            source="chat",
            confidence=0.5,
            memory_type=None)
        await save_memory_single(session, owner, fact="Настя любит чай", contact_id=999,
            source="chat",
            confidence=0.5,
            memory_type=None)
        await session.commit()

    result = await recall(123460, contact_id=999, limit=10)
    facts_text = " ".join(f.fact for f in result.facts)
    reasons = " ".join(f.reason for f in result.facts)
    assert "люблю кофе" in facts_text
    assert "любит чай" in facts_text
    assert "тебе" in reasons or "контакте" in reasons or "свежий" in reasons


@pytest.mark.asyncio
async def test_format_recall_for_prompt():
    """Форматтер выдаёт XML-тег <recall_context>."""
    async with get_session() as session:
        owner = await get_or_create_user(session, 123461)
        await save_memory_single(session, owner, fact="памятный факт", pinned=True,
            source="chat",
            confidence=0.5,
            memory_type=None)
        await session.commit()

    result = await recall(123461, limit=5)
    text = format_recall_for_prompt(result)
    assert "<recall_context>" in text
    assert "</recall_context>" in text
    assert "памятный факт" in text


@pytest.mark.asyncio
async def test_no_facts_graceful():
    """Пустая память — не падает, возвращает пустой результат."""
    async with get_session() as session:
        await get_or_create_user(session, 123462)
        await session.commit()

    result = await recall(123462, limit=5)
    assert result.facts == []
    assert result.meta["total_active"] == 0


# ---------------------------------------------------------------------------
# Cache tests (mock database — test logic only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_cache_hit():
    """Второй вызов с тем же ключом возвращает кешированный результат."""
    import src.core.memory.memory_recall as mr_mod

    fake_result = RecallResult(
        facts=[RecalledFact(fact="кешированный факт", reason="📌 закреплён")],
        meta={"cached": True},
    )
    cache_key = mr_mod._make_recall_cache_key(
        telegram_id=123456,
        query="test query",
        contact_id=None,
        mode="deep",
        limit=5,
        offset=0,
        include_self=True,
        include_pinned=True,
        include_tasks=True,
        include_deep=True,
        semantic_threshold=0.6,
    )

    # Напрямую тестируем логику кеша через TTLCache API.
    await mr_mod._recall_cache.clear()
    await mr_mod._recall_cache.set(cache_key, fake_result, ttl=30)
    cached = await mr_mod._recall_cache.get(cache_key)
    assert cached is not None
    assert cached.facts[0].fact == "кешированный факт"


@pytest.mark.asyncio
async def test_recall_cache_expiry():
    """Кеш протухает через 60 секунд."""
    import src.core.memory.memory_recall as mr_mod

    fake_result = RecallResult(
        facts=[RecalledFact(fact="просроченный кеш", reason="🆕 свежий")],
        meta={"cached": True},
    )
    cache_key = mr_mod._make_recall_cache_key(
        telegram_id=123457,
        query="stale",
        contact_id=None,
        mode="deep",
        limit=5,
        offset=0,
        include_self=True,
        include_pinned=True,
        include_tasks=True,
        include_deep=True,
        semantic_threshold=0.6,
    )

    # TTL=0.1 означает что кеш протухнет почти мгновенно.
    await mr_mod._recall_cache.clear()
    await mr_mod._recall_cache.set(cache_key, fake_result, ttl=0.1)
    await asyncio.sleep(0.2)
    cached = await mr_mod._recall_cache.get(cache_key)
    assert cached is None


@pytest.mark.asyncio
async def test_recall_cache_key_includes_limit():
    """Кеш не должен отдавать limit=1 на следующий запрос limit=5."""
    import src.core.memory.memory_recall as mr_mod

    telegram_id = 123477
    await mr_mod._recall_cache.clear()

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        for i in range(5):
            await save_memory_single(session, owner, fact=f"факт {i}", confidence=0.9,
                source="chat",
                memory_type=None)
        await session.commit()

    small = await recall(telegram_id, limit=1, mode="normal")
    large = await recall(telegram_id, limit=5, mode="normal")

    assert len(small.facts) == 1
    assert len(large.facts) >= 5


# ---------------------------------------------------------------------------
# MMR rerank tests (pure logic, no DB)
# ---------------------------------------------------------------------------


class TestMMRRerank:
    """Тесты алгоритма Maximal Marginal Relevance re-ranking."""

    def test_mmr_rerank_diversifies(self):
        """Дублирующиеся факты получают разные ранги."""
        facts = [
            {"score": 0.9, "fact": "я люблю кофе"},
            {"score": 0.9, "fact": "я люблю кофе"},  # почти дубликат
            {"score": 0.8, "fact": "я работаю в IT"},
            {"score": 0.7, "fact": "я живу в Москве"},
        ]
        result = _mmr_rerank(facts)
        # Первый — самый релевантный
        assert result[0]["fact"] == "я люблю кофе"
        # Дубликат должен быть отодвинут ниже уникального контента
        # (второй "я люблю кофе" имеет max_sim=1.0 с первым,
        #  поэтому его MMR = 0.7*0.9 - 0.3*1.0 = 0.33,
        #  а "я работаю в IT" имеет MMR = 0.7*0.8 - 0.3*0.0 = 0.56)
        assert result[1]["fact"] != "я люблю кофе", (
            "Дубликат не должен быть на втором месте"
        )

    def test_mmr_rerank_empty(self):
        """Пустой список — пустой результат."""
        assert _mmr_rerank([]) == []

    def test_mmr_rerank_single(self):
        """Один факт — возвращается как есть."""
        facts = [{"score": 0.5, "fact": "один факт"}]
        result = _mmr_rerank(facts)
        assert len(result) == 1
        assert result[0]["fact"] == "один факт"

    def test_jaccard_similarity_identical(self):
        """Jaccard для одинаковых строк = 1.0."""
        sim = _jaccard_similarity("я люблю кофе", "я люблю кофе")
        assert sim == 1.0

    def test_jaccard_similarity_different(self):
        """Jaccard для разных строк = 0.0."""
        sim = _jaccard_similarity("я люблю кофе", "завтра еду в Сочи")
        assert sim == 0.0

    def test_jaccard_similarity_partial(self):
        """Jaccard для частично пересекающихся строк."""
        sim = _jaccard_similarity("я люблю кофе", "я люблю чай")
        # пересечение: я, люблю (2), объединение: я, люблю, кофе, чай (4)
        assert sim == 0.5


# ---------------------------------------------------------------------------
# RRF tests (pure logic, no DB)
# ---------------------------------------------------------------------------


class TestRRF:
    """Тесты Reciprocal Rank Fusion."""

    def test_rrf_weights_positions(self):
        """RRF использует позицию, а не raw score."""
        # Два списка с одинаковыми ID, но разными позициями
        vector_results = [(1, 0.99), (2, 0.50), (3, 0.30)]
        keyword_results = [(2, 0.80), (1, 0.60), (3, 0.10)]

        result = reciprocal_rank_fusion(vector_results, keyword_results)

        # ID=1: позиции 1 (vector) и 2 (keyword)
        #   rrf = 1/(60+1) + 1/(60+2) = 1/61 + 1/62 ≈ 0.0164 + 0.0161 ≈ 0.0325
        # ID=2: позиции 2 (vector) и 1 (keyword)
        #   rrf = 1/(60+2) + 1/(60+1) = 1/62 + 1/61 ≈ 0.0325
        # ID=3: позиции 3 (vector) и 3 (keyword)
        #   rrf = 1/(60+3) + 1/(60+3) ≈ 0.0159 + 0.0159 ≈ 0.0317
        # ID=1 и ID=2 должны иметь близкие (или равные) скоры
        scores = {mem_id: round(score, 6) for mem_id, score in result}
        assert scores[1] == scores[2], (
            f"RRF для ID=1 и ID=2 должны быть равны (симметричные позиции), "
            f"получено: {scores}"
        )
        assert scores[3] < scores[1], (
            "ID=3 на 3-м месте в обоих списках должен иметь ниже score"
        )

    def test_rrf_empty_inputs(self):
        """Пустые входные списки — пустой результат."""
        result = reciprocal_rank_fusion(None, None)
        assert result == []

    def test_rrf_single_list(self):
        """Только один список — работает как rank-based scoring."""
        vector_results = [(100, 0.9), (200, 0.8), (300, 0.5)]
        result = reciprocal_rank_fusion(vector_results)
        assert len(result) == 3
        assert result[0][0] == 100  # первый в ранжировании
        assert result[1][0] == 200
        assert result[2][0] == 300

    def test_rrf_k_value_affects_score(self):
        """Разное k даёт разное распределение скоров."""
        results = [(1, 0.9), (2, 0.5)]
        r1 = reciprocal_rank_fusion(results, k=0)
        r2 = reciprocal_rank_fusion(results, k=60)
        r3 = reciprocal_rank_fusion(results, k=1000)
        # С разными k скоры должны отличаться
        assert r1[0][1] != r2[0][1]
        assert r2[0][1] != r3[0][1]
        # Порядок сохраняется
        assert r1[0][0] == r2[0][0] == r3[0][0] == 1
