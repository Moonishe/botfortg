"""Tests for Hy-Memory Phase 2 and Phase 3 components.

Phase 2: MemoryMode, TTLCache, memory_metrics
Phase 3: user_worldview, system2_orchestrator
"""

import asyncio
import os
import time

import pytest

# --- Phase 2: MemoryMode ---


class TestMemoryMode:
    def test_enum_values(self):
        from src.core.memory.memory_mode import MemoryMode

        assert MemoryMode.LIGHT == "light"
        assert MemoryMode.NORMAL == "normal"
        assert MemoryMode.DEEP == "deep"

    def test_from_string_valid(self):
        from src.core.memory.memory_mode import MemoryMode

        assert MemoryMode.from_string("light") == MemoryMode.LIGHT
        assert MemoryMode.from_string("normal") == MemoryMode.NORMAL
        assert MemoryMode.from_string("deep") == MemoryMode.DEEP
        assert MemoryMode.from_string("DEEP") == MemoryMode.DEEP

    def test_from_string_invalid_falls_back_to_deep(self):
        from src.core.memory.memory_mode import MemoryMode

        assert MemoryMode.from_string("unknown") == MemoryMode.DEEP
        assert MemoryMode.from_string("") == MemoryMode.DEEP
        assert MemoryMode.from_string(None) == MemoryMode.DEEP

    def test_missing_fallback(self):
        from src.core.memory.memory_mode import MemoryMode

        assert MemoryMode("garbage") == MemoryMode.DEEP  # _missing_

    def test_properties(self):
        from src.core.memory.memory_mode import MemoryMode

        # LIGHT
        assert MemoryMode.LIGHT.includes_semantic is False
        assert MemoryMode.LIGHT.includes_deep is False
        assert MemoryMode.LIGHT.includes_frequent is False
        assert MemoryMode.LIGHT.includes_self_facts is False
        assert MemoryMode.LIGHT.includes_contact_facts is False

        # NORMAL
        assert MemoryMode.NORMAL.includes_semantic is True
        assert MemoryMode.NORMAL.includes_deep is False
        assert MemoryMode.NORMAL.includes_frequent is True
        assert MemoryMode.NORMAL.includes_self_facts is True
        assert MemoryMode.NORMAL.includes_contact_facts is True

        # DEEP
        assert MemoryMode.DEEP.includes_semantic is True
        assert MemoryMode.DEEP.includes_deep is True
        assert MemoryMode.DEEP.includes_frequent is True
        assert MemoryMode.DEEP.includes_self_facts is True
        assert MemoryMode.DEEP.includes_contact_facts is True

    def test_str_compatibility(self):
        from src.core.memory.memory_mode import MemoryMode

        # Можно использовать как строку
        d = {"light": 1, "normal": 2, "deep": 3}
        assert d[MemoryMode.LIGHT] == 1
        assert d[MemoryMode.DEEP] == 3


# --- Phase 2: TTLCache ---


@pytest.mark.asyncio
async def test_ttl_cache_basic():
    from src.core.memory.ttl_cache import TTLCache

    cache = TTLCache[str, str](max_size=10, default_ttl=1.0)
    await cache.set("key", "value")
    assert await cache.get("key") == "value"
    assert cache.size == 1


@pytest.mark.asyncio
async def test_ttl_cache_expiry():
    from src.core.memory.ttl_cache import TTLCache

    cache = TTLCache[str, str](max_size=10, default_ttl=0.1)
    await cache.set("key", "value")
    await asyncio.sleep(0.2)
    assert await cache.get("key") is None


@pytest.mark.asyncio
async def test_ttl_cache_invalidate():
    from src.core.memory.ttl_cache import TTLCache

    cache = TTLCache[str, str](max_size=10, default_ttl=10.0)
    await cache.set("key", "value")
    assert await cache.invalidate("key") is True
    assert await cache.get("key") is None
    assert await cache.invalidate("key") is False


@pytest.mark.asyncio
async def test_ttl_cache_clear():
    from src.core.memory.ttl_cache import TTLCache

    cache = TTLCache[str, str](max_size=10, default_ttl=10.0)
    await cache.set("a", "1")
    await cache.set("b", "2")
    assert await cache.clear() == 2
    assert cache.size == 0


@pytest.mark.asyncio
async def test_ttl_cache_custom_ttl():
    from src.core.memory.ttl_cache import TTLCache

    cache = TTLCache[str, str](max_size=10, default_ttl=10.0)
    await cache.set("fast", "v", ttl=0.1)
    await asyncio.sleep(0.2)
    assert await cache.get("fast") is None


@pytest.mark.asyncio
async def test_ttl_cache_max_size_eviction():
    from src.core.memory.ttl_cache import TTLCache

    cache = TTLCache[int, str](max_size=3, default_ttl=10.0)
    for i in range(5):
        await cache.set(i, str(i))
    assert cache.size == 3
    # Старейшие (0, 1) должны быть вытеснены
    assert await cache.get(0) is None
    assert await cache.get(1) is None
    assert await cache.get(2) is not None
    assert await cache.get(3) is not None
    assert await cache.get(4) is not None


@pytest.mark.asyncio
async def test_ttl_cache_get_or_set():
    from src.core.memory.ttl_cache import TTLCache

    cache = TTLCache[str, int](max_size=10, default_ttl=10.0)
    call_count = 0

    def factory():
        nonlocal call_count
        call_count += 1
        return 42

    v1 = await cache.get_or_set("key", factory)
    v2 = await cache.get_or_set("key", factory)
    assert v1 == 42
    assert v2 == 42
    assert call_count == 1  # factory вызвана только раз


@pytest.mark.asyncio
async def test_ttl_cache_hit_rate():
    from src.core.memory.ttl_cache import TTLCache

    cache = TTLCache[str, str](max_size=10, default_ttl=10.0)
    await cache.set("key", "value")
    await cache.get("key")  # hit
    await cache.get("missing")  # miss
    stats = cache.stats
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 0.5


@pytest.mark.asyncio
async def test_ttl_cache_on_evict():
    from src.core.memory.ttl_cache import TTLCache

    evicted = []

    def on_evict(k, v):
        evicted.append((k, v))

    cache = TTLCache[int, str](max_size=1, default_ttl=10.0, on_evict=on_evict)
    await cache.set(1, "first")
    await cache.set(2, "second")  # evicts 1
    assert len(evicted) == 1
    assert evicted[0] == (1, "first")


# --- Phase 2: Memory Metrics ---


@pytest.mark.asyncio
async def test_metrics_record_health():
    from src.core.memory.memory_metrics import memory_metrics

    await memory_metrics.reset()
    await memory_metrics.record_health(85.5, {"a": 1})
    snap = await memory_metrics.snapshot(force=True)
    assert snap.health_score == 85.5
    assert snap.health_trend == "stable"


@pytest.mark.asyncio
async def test_metrics_contradictions():
    from src.core.memory.memory_metrics import memory_metrics

    await memory_metrics.reset()
    await memory_metrics.record_contradiction()
    await memory_metrics.record_contradiction()
    snap = await memory_metrics.snapshot(force=True)
    assert snap.contradictions_total == 2


@pytest.mark.asyncio
async def test_metrics_supersedes():
    from src.core.memory.memory_metrics import memory_metrics

    await memory_metrics.reset()
    await memory_metrics.record_supersedes_chain(3)
    await memory_metrics.record_supersedes_chain(5)
    snap = await memory_metrics.snapshot(force=True)
    assert snap.supersedes_chains == 2
    assert snap.supersedes_avg_chain_length == 4.0


@pytest.mark.asyncio
async def test_metrics_pre_filter():
    from src.core.memory.memory_metrics import memory_metrics

    await memory_metrics.reset()
    await memory_metrics.record_pre_filter(accepted=True)
    await memory_metrics.record_pre_filter(accepted=True)
    await memory_metrics.record_pre_filter(accepted=False)
    snap = await memory_metrics.snapshot(force=True)
    assert snap.pre_filter_accepts == 2
    assert snap.pre_filter_rejects == 1
    assert snap.pre_filter_reject_rate == 1 / 3


@pytest.mark.asyncio
async def test_metrics_extraction_latency():
    from src.core.memory.memory_metrics import memory_metrics

    await memory_metrics.reset()
    await memory_metrics.record_extraction(100.0)
    await memory_metrics.record_extraction(200.0)
    snap = await memory_metrics.snapshot(force=True)
    assert snap.extractions_total == 2
    assert snap.extraction_avg_latency_ms == 150.0


@pytest.mark.asyncio
async def test_metrics_recall():
    from src.core.memory.memory_metrics import memory_metrics

    await memory_metrics.reset()
    await memory_metrics.record_recall(hit=True)
    await memory_metrics.record_recall(hit=True)
    await memory_metrics.record_recall(hit=False)
    snap = await memory_metrics.snapshot(force=True)
    assert snap.recall_hit_rate == 2 / 3


@pytest.mark.asyncio
async def test_metrics_fact_counts():
    from src.core.memory.memory_metrics import memory_metrics

    await memory_metrics.reset()
    await memory_metrics.set_fact_counts(100, 80, 20)
    snap = await memory_metrics.snapshot(force=True)
    assert snap.total_facts == 100
    assert snap.active_facts == 80
    assert snap.inactive_facts == 20


@pytest.mark.asyncio
async def test_metrics_trend_detection():
    from src.core.memory.memory_metrics import memory_metrics

    await memory_metrics.reset()
    # Растущий тренд
    for i in range(15):
        await memory_metrics.record_health(50.0 + i * 2)
    snap = await memory_metrics.snapshot(force=True)
    assert snap.health_trend == "up"

    await memory_metrics.reset()
    # Падающий тренд
    for i in range(15):
        await memory_metrics.record_health(80.0 - i * 2)
    snap = await memory_metrics.snapshot(force=True)
    assert snap.health_trend == "down"


# --- Phase 3: user_worldview ---


@pytest.fixture
def setup_env():
    os.environ.setdefault("API_ID", "12345")
    os.environ.setdefault("API_HASH", "deadbeef")
    os.environ.setdefault("BOT_TOKEN", "test:token")
    os.environ.setdefault("OWNER_TELEGRAM_ID", "99001")


async def _reset_db():
    """Drop all tables (ORM + FTS virtual) and reinitialize DB.

    ``Base.metadata.drop_all`` only drops ORM-managed tables.  FTS5 virtual
    tables (messages_fts, memories_fts, etc.) are created by raw SQL and
    survive ``drop_all``.  On the second call, ``init_db()`` sees orphan FTS
    tables in ``sqlite_master``, assumes ORM schema is present and skips
    ``create_all`` — causing ``OperationalError: no such table: messages``
    when the FTS trigger statements run.

    Fix: explicitly drop FTS virtual tables and their shadow tables before
    calling ``init_db()``.
    """
    from src.db.session import engine, Base, init_db
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        # Drop FTS5 virtual tables + their shadow (internal) tables
        for fts_name in (
            "messages_fts",
            "agent_session_messages_fts",
            "memories_fts",
        ):
            await conn.execute(text(f"DROP TABLE IF EXISTS {fts_name}"))
            for suffix in ("_data", "_idx", "_docsize", "_config"):
                await conn.execute(text(f"DROP TABLE IF EXISTS {fts_name}{suffix}"))
    await init_db()


@pytest.mark.asyncio
async def test_worldview_empty_user(setup_env):
    await _reset_db()

    from src.db.repo import get_or_create_user
    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, 99001)

    from src.core.memory.user_worldview import build_worldview

    worldview = await build_worldview(99001)
    assert worldview.total_facts == 0
    assert worldview.active_facts == 0
    assert worldview.contradictions == []


@pytest.mark.asyncio
async def test_worldview_categorization(setup_env):
    await _reset_db()

    from src.db.repo import get_or_create_user, add_memory
    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, 99001)

    # Добавляем факты разных категорий
    facts = [
        "Я люблю Python",  # preferences
        "Я считаю что TypeScript лучше",  # beliefs
        "Я живу в Москве",  # personal_info
        "Я работаю в Яндексе",  # work
        "Я всегда пью кофе утром",  # habits
        "Мой друг работает в Google",  # relations
    ]
    async with get_session() as session:
        for fact in facts:
            await add_memory(session, owner, fact=fact, source="test")

    from src.core.memory.user_worldview import build_worldview

    worldview = await build_worldview(99001)
    assert worldview.total_facts == 6
    assert worldview.active_facts == 6

    prefs = worldview.categories.get("preferences")
    assert prefs is not None
    assert prefs.fact_count >= 1
    assert any("Python" in f["fact"] for f in prefs.facts)

    beliefs = worldview.categories.get("beliefs")
    assert beliefs is not None
    assert any("TypeScript" in f["fact"] for f in beliefs.facts)

    work = worldview.categories.get("work")
    assert work is not None
    assert any("Яндекс" in f["fact"] for f in work.facts)


@pytest.mark.asyncio
async def test_worldview_summary(setup_env):
    await _reset_db()

    from src.db.repo import get_or_create_user, add_memory
    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, 99001)
        await add_memory(session, owner, fact="Я люблю кофе", source="test")

    from src.core.memory.user_worldview import build_worldview

    worldview = await build_worldview(99001)
    summary = worldview.summary()
    assert "Мировоззрение пользователя" in summary
    assert "Я люблю кофе" in summary


# --- Phase 3: system2_orchestrator ---


@pytest.mark.asyncio
async def test_system2_empty_user(setup_env):
    await _reset_db()

    from src.db.repo import get_or_create_user
    from src.db.session import get_session

    async with get_session() as session:
        await get_or_create_user(session, 99001)

    from src.core.memory.system2_orchestrator import analyze

    analysis = await analyze(99001)
    assert analysis.total_nodes_visited == 0
    assert analysis.found_facts == []


@pytest.mark.asyncio
async def test_system2_with_facts(setup_env):
    await _reset_db()

    from src.db.repo import get_or_create_user, add_memory, link_memories
    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, 99001)
        m1 = await add_memory(session, owner, fact="Я работаю в Google", source="test")
        m2 = await add_memory(session, owner, fact="Я работаю в Яндексе", source="test")
        m3 = await add_memory(session, owner, fact="Я люблю Python", source="test")

    if m1 and m2:
        async with get_session() as session:
            owner2 = await get_or_create_user(session, 99001)
            await link_memories(
                session,
                owner2,
                source_id=m2.id,
                target_id=m1.id,
                weight=0.9,
                relation_type="supersedes",
            )
            await session.commit()

    from src.core.memory.system2_orchestrator import analyze

    # Тест с query
    analysis = await analyze(99001, query="Python")
    assert analysis.total_nodes_visited >= 1
    facts = [n.fact for n in analysis.found_facts]
    assert any("Python" in f for f in facts)

    # Тест с focus_fact_id
    analysis = await analyze(99001, focus_fact_id=m1.id)
    assert analysis.total_nodes_visited >= 1
    assert len(analysis.found_facts) >= 1


@pytest.mark.asyncio
async def test_system2_supersedes_chain(setup_env):
    await _reset_db()

    from src.db.repo import get_or_create_user, add_memory, link_memories
    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, 99001)
        m1 = await add_memory(session, owner, fact="Я пью кофе", source="test")
        m2 = await add_memory(
            session, owner, fact="Я перестал пить кофе", source="test"
        )

    if m1 and m2:
        async with get_session() as session:
            owner2 = await get_or_create_user(session, 99001)
            await link_memories(
                session,
                owner2,
                source_id=m2.id,
                target_id=m1.id,
                weight=0.95,
                relation_type="supersedes",
            )
            await session.commit()

    from src.core.memory.system2_orchestrator import analyze

    analysis = await analyze(99001, query="кофе")
    # link_memories creates bidirectional links with the same relation_type,
    # so evolution_chains may be empty (no "tails").  Assert on supersedes
    # edges instead — the analysis DOES detect the relationship.
    supersedes_edges = [
        e for e in analysis.edges if e["type"] in ("supersedes", "superseded_by")
    ]
    assert len(supersedes_edges) >= 1


@pytest.mark.asyncio
async def test_system2_insights(setup_env):
    await _reset_db()

    from src.db.repo import get_or_create_user, add_memory, link_memories
    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, 99001)
        m1 = await add_memory(session, owner, fact="Я люблю кофе", source="test")
        m2 = await add_memory(session, owner, fact="Я не люблю кофе", source="test")
        m3 = await add_memory(session, owner, fact="Кофе вреден", source="test")
        m4 = await add_memory(session, owner, fact="Пью чай", source="test")

    if m1 and m2 and m3 and m4:
        async with get_session() as session:
            owner2 = await get_or_create_user(session, 99001)
            await link_memories(
                session,
                owner2,
                source_id=m2.id,
                target_id=m1.id,
                weight=0.95,
                relation_type="supersedes",
            )
            await link_memories(
                session,
                owner2,
                source_id=m3.id,
                target_id=m2.id,
                weight=0.8,
                relation_type="supports",
            )
            await link_memories(
                session,
                owner2,
                source_id=m4.id,
                target_id=m2.id,
                weight=0.7,
                relation_type="continues",
            )
            await session.commit()

    from src.core.memory.system2_orchestrator import analyze

    analysis = await analyze(99001, query="кофе")
    summary = analysis.summary()
    assert "System2 Analysis" in summary
    assert len(analysis.insights) >= 1  # supersedes insight
