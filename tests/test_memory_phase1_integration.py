"""Интеграционные тесты Phase 1: pre-filter + supersedes pipeline."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.db.session import get_session
from src.db.repo import add_memory, get_or_create_user, link_memories
from src.core.memory.pre_filter import should_extract, score_transcript
from src.core.memory.memory_chain import (
    follow_supersedes_chain,
    RELATION_EMOJI,
    RELATION_WORD,
)

OWNER_TG_ID = 99002


@pytest.fixture(autouse=True)
def setup_db():
    """Re-create tables before each test (in-memory SQLite)."""
    from src.db.session import engine, Base, init_db
    from sqlalchemy import text

    async def _recreate():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
            await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
        await init_db()

    asyncio.run(_recreate())


def _utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _make_owner(tg_id: int = OWNER_TG_ID):
    async with get_session() as session:
        return await get_or_create_user(session, tg_id)


async def _make_memory(owner, fact: str, created_offset_sec: int = 0):
    """Create a Memory with controllable created_at offset (seconds from now)."""
    from src.db.models import Memory

    async with get_session() as session:
        m = await add_memory(
            session,
            owner,
            fact=fact,
            source="chat",
            confidence=0.9,
        )
        if m is None:
            return None
        new_ts = datetime.now(timezone.utc) - timedelta(seconds=created_offset_sec)
        m.created_at = new_ts
        await session.commit()
        return m


# ══════════════════════════════════════════════════════════════════════
# Integration: pre-filter gate + supersedes on Memory model
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pre_filter_passes_then_supersedes_memory_created():
    """Transcript passes pre-filter, LLM assigns relation_type='supersedes'.

    The Memory model has a relation_type field (not just MemoryLink).
    Verifies the full pipeline from heuristic gate through fact extraction.
    """
    owner = await _make_owner()

    # --- Step 1: pre-filter says YES for fact-heavy transcript ---
    transcript = (
        "Я работаю в Яндексе уже 5 лет, а раньше работал в Google. "
        "Теперь занимаюсь разработкой на Python."
    )
    score = score_transcript(transcript)
    assert score >= 0.3, f"Expected pre-filter score >= 0.3, got {score}"
    assert should_extract(transcript) is True

    # --- Step 2: simulate LLM result with supersedes relation ---
    # The LLM returns facts with relation_to_index pointing to previous facts
    # in the same response. We replicate the parsing and Memory creation.
    from src.db.models import Memory

    async with get_session() as session:
        owner2 = await get_or_create_user(session, OWNER_TG_ID)

        # Fact 0: old job (will be superseded)
        old_mem = Memory(
            user_id=owner2.id,
            fact="работал в Google",
            sentiment="neutral",
            source="chat",
            confidence=0.85,
            importance=0.7,
            memory_type="personal",
            relation_type=None,
        )
        session.add(old_mem)
        await session.flush()

        # Fact 1: new job, supersedes fact 0
        new_mem = Memory(
            user_id=owner2.id,
            fact="работаю в Яндексе",
            sentiment="positive",
            source="chat",
            confidence=0.9,
            importance=0.8,
            memory_type="personal",
            relation_type="supersedes",
            related_memory_id=old_mem.id,
        )
        session.add(new_mem)
        await session.commit()

        assert new_mem.relation_type == "supersedes"
        assert new_mem.related_memory_id == old_mem.id

        # Also create the MemoryLink for the supersedes chain traversal
        await link_memories(
            session,
            owner2,
            source_id=new_mem.id,
            target_id=old_mem.id,
            weight=0.95,
            relation_type="supersedes",
        )
        await session.commit()

    # --- Step 3: chain traversal returns both facts ---
    async with get_session() as session:
        chain = await follow_supersedes_chain(session, owner, old_mem.id)

    assert len(chain) == 2
    assert chain[0]["memory_id"] == old_mem.id
    assert chain[0]["fact"] == "работал в Google"
    assert chain[0]["is_head"] is False
    assert chain[1]["memory_id"] == new_mem.id
    assert chain[1]["fact"] == "работаю в Яндексе"
    assert chain[1]["is_head"] is True


# ══════════════════════════════════════════════════════════════════════
# Integration: pre-filter + contradiction-confirm → supersedes chain
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_contradiction_confirm_then_follow_supersedes_chain():
    """User confirms contradiction → supersedes link → chain traversal works.

    Simulates: old fact "Люблю острое", new fact "Не люблю острое",
    contradiction detector creates supersedes link, then
    follow_supersedes_chain returns both in chronological order.
    """
    owner = await _make_owner()
    old = await _make_memory(owner, "Люблю острое", created_offset_sec=300)
    new = await _make_memory(owner, "Не люблю острое", created_offset_sec=0)

    async with get_session() as session:
        owner2 = await get_or_create_user(session, OWNER_TG_ID)
        await link_memories(
            session,
            owner2,
            source_id=new.id,
            target_id=old.id,
            weight=0.95,
            relation_type="supersedes",
        )
        await session.commit()

        chain = await follow_supersedes_chain(session, owner2, new.id)

    # Chain: old → new (chronological order)
    assert len(chain) == 2
    assert chain[0]["memory_id"] == old.id
    assert chain[0]["fact"] == "Люблю острое"
    assert chain[0]["is_head"] is False
    assert chain[1]["memory_id"] == new.id
    assert chain[1]["fact"] == "Не люблю острое"
    assert chain[1]["is_head"] is True


# ══════════════════════════════════════════════════════════════════════
# Resilience: cycle in supersedes chain does not infinite-loop
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_supersedes_cycle_terminates_via_link_memories():
    """link_memories creates bidirectional edges → A↔B cycle.

    follow_supersedes_chain(A) must terminate and return at most 2 nodes.
    This is an integration-level test: link_memories naturally creates
    reverse edges, so any supersedes pair is a cycle. The chain function
    must handle this without manual edge manipulation.
    """
    owner = await _make_owner()
    a = await _make_memory(owner, "факт A", created_offset_sec=60)
    b = await _make_memory(owner, "факт B", created_offset_sec=0)

    # link_memories creates B→A + reverse A→B (bidirectional supersedes)
    async with get_session() as session:
        owner2 = await get_or_create_user(session, OWNER_TG_ID)
        await link_memories(
            session,
            owner2,
            source_id=b.id,
            target_id=a.id,
            weight=0.9,
            relation_type="supersedes",
        )
        await session.commit()

        # Verify both directions exist
        from src.db.models import MemoryLink
        from sqlalchemy import select

        result = await session.execute(
            select(MemoryLink).where(
                MemoryLink.user_id == owner2.id,
                MemoryLink.relation_type == "supersedes",
            )
        )
        links = result.scalars().all()
        # link_memories creates 2 rows (forward + reverse)
        assert len(links) == 2

        # Now traverse — must terminate with at most 2 nodes (not infinite)
        chain = await follow_supersedes_chain(session, owner2, a.id, max_depth=20)

    # Should have exactly 2 unique nodes (A and B), no duplicates
    ids = {c_["memory_id"] for c_ in chain}
    assert ids == {a.id, b.id}
    assert len(chain) == 2


# ══════════════════════════════════════════════════════════════════════
# Pre-filter edge cases (integration with empty transcripts)
# ══════════════════════════════════════════════════════════════════════


def test_pre_filter_rejects_empty_and_noise_transcripts():
    """Empty or pure-noise transcripts must be rejected by pre-filter.

    This guards the LLM call path: if should_extract returns False,
    extract_and_save_memories returns 0 without calling LLM.
    """
    # Empty
    assert score_transcript("") == 0.0
    assert should_extract("") is False

    # Whitespace-only (split() yields empty list → word_count=0 → no noise
    # penalty; but length < 20 → -0.5 → clamp to 0.0)
    assert should_extract("   ") is False

    # Single short noise word
    assert should_extract("привет") is False

    # Multi-word noise
    assert should_extract("привет как дела бро") is False


# ══════════════════════════════════════════════════════════════════════
# Independence: pre_filter state does not affect chain traversal
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pre_filter_independent_from_supersedes_chain():
    """follow_supersedes_chain works regardless of pre_filter settings.

    The chain traversal reads from the database and does not involve
    the pre-filter heuristic. Changing pre_filter_enabled or
    pre_filter_min_score should have zero impact on chain results.
    """
    owner = await _make_owner()
    old = await _make_memory(owner, "старый факт", created_offset_sec=120)
    new = await _make_memory(owner, "новый факт", created_offset_sec=0)

    async with get_session() as session:
        owner2 = await get_or_create_user(session, OWNER_TG_ID)
        await link_memories(
            session,
            owner2,
            source_id=new.id,
            target_id=old.id,
            weight=0.95,
            relation_type="supersedes",
        )
        await session.commit()

    # Run chain traversal (does NOT read settings.pre_filter_enabled)
    async with get_session() as session:
        chain = await follow_supersedes_chain(session, owner, old.id)

    assert len(chain) == 2
    assert chain[1]["is_head"] is True
    assert chain[1]["memory_id"] == new.id

    # Verify: pre_filter settings exist but chain does NOT import them
    import inspect

    src = inspect.getsource(follow_supersedes_chain)
    assert "pre_filter" not in src, (
        "follow_supersedes_chain must not depend on pre_filter settings"
    )


# ══════════════════════════════════════════════════════════════════════
# RELATION_EMOJI / RELATION_WORD include supersedes
# ══════════════════════════════════════════════════════════════════════


def test_supersedes_in_relation_constants():
    """Phase 1 requirement: supersedes is in RELATION_EMOJI and RELATION_WORD."""
    assert "supersedes" in RELATION_EMOJI, (
        f"RELATION_EMOJI keys: {list(RELATION_EMOJI.keys())}"
    )
    assert "supersedes" in RELATION_WORD, (
        f"RELATION_WORD keys: {list(RELATION_WORD.keys())}"
    )
    assert RELATION_EMOJI["supersedes"] == "🔄"
    assert RELATION_WORD["supersedes"] == "обновлено на"


# ══════════════════════════════════════════════════════════════════════
# Phase 1.1: smoke test that free_text.py actually enqueues extract
# ══════════════════════════════════════════════════════════════════════


def test_free_text_enqueues_extract_with_pre_filter_guard():
    """Fix #1 (HIGH): free_text.py must enqueue extract job, gated by pre_filter.

    Без этого extract_and_save_memories() НЕ вызывается в main flow →
    supersedes evolution chains в Stage 0c не работают (5-минутное окно
    в check_contradiction_response остаётся пустым).

    Этот AST-тест проверяет код _process_text: должен быть блок
    `if should_extract(raw): await enqueue(MemoryJob(...))` ДО Stage 0
    (smart emoji replies). Если кто-то его удалит — supersedes сломается
    в normal dialog flow, и тест сразу укажет на регрессию.
    """
    import ast
    import inspect
    from src.bot.handlers import free_text

    src = inspect.getsource(free_text._process_text)
    tree = ast.parse(src)

    # 1. _process_text должен импортировать MemoryJob и enqueue
    found_memory_job = False
    found_enqueue = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "src.core.memory._queue_core"
        ):
            for alias in node.names:
                if alias.name == "MemoryJob":
                    found_memory_job = True
                if alias.name == "enqueue":
                    found_enqueue = True

    assert found_memory_job, (
        "free_text._process_text must import MemoryJob from _queue_core"
    )
    assert found_enqueue, "free_text._process_text must import enqueue from _queue_core"

    # 2. _process_text должен импортировать should_extract
    found_pre_filter = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "src.core.memory.pre_filter"
        ):
            for alias in node.names:
                if alias.name == "should_extract":
                    found_pre_filter = True
    assert found_pre_filter, (
        "free_text._process_text must import should_extract from pre_filter"
    )

    # 3. Должен быть вызов should_extract(raw) с последующим enqueue(MemoryJob)
    found_guard = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            # ищем If-блок, у которого test содержит вызов should_extract
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Call):
                    if (
                        isinstance(sub.func, ast.Name)
                        and sub.func.id == "should_extract"
                    ):
                        # внутри тела if должен быть enqueue(MemoryJob(...))
                        for inner in ast.walk(node):
                            if isinstance(inner, ast.Await):
                                if isinstance(inner.value, ast.Call):
                                    call = inner.value
                                    if (
                                        isinstance(call.func, ast.Name)
                                        and call.func.id == "enqueue"
                                    ):
                                        if call.args and isinstance(
                                            call.args[0], ast.Call
                                        ):
                                            if (
                                                isinstance(call.args[0].func, ast.Name)
                                                and call.args[0].func.id == "MemoryJob"
                                            ):
                                                found_guard = True

    assert found_guard, (
        "free_text._process_text must contain "
        "`if should_extract(raw): await enqueue(MemoryJob(...))` block. "
        "Without it, supersedes evolution chains do not work in main dialog flow."
    )


# ══════════════════════════════════════════════════════════════════════
# Phase 1.1: relation_type whitelist (Fix #3)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_link_memories_drops_invalid_relation_type():
    """Fix #3: link_memories должна отсекать LLM-галлюцинации relation_type.

    LLM может вернуть «supersede» (без 's') или «replaces». Без whitelist
    они молча попадут в БД и не будут найдены ни одним relation-фильтром.
    Тест проверяет: невалидный relation_type → возвращается link,
    но relation_type=None (silent drop + warning).
    """
    owner = await _make_owner()
    a = await _make_memory(owner, "факт A")
    b = await _make_memory(owner, "факт B")

    async with get_session() as session:
        owner2 = await get_or_create_user(session, OWNER_TG_ID)
        # «supersede» (без 's') — типичная LLM-галлюцинация
        result = await link_memories(
            session,
            owner2,
            source_id=b.id,
            target_id=a.id,
            weight=0.5,
            relation_type="supersede",
        )
        await session.commit()

        # Link всё-таки создан (важно: факт не теряется),
        # но relation_type принудительно None
        assert result is not None
        assert result.relation_type is None, (
            f"Invalid relation_type should be dropped, got {result.relation_type!r}"
        )

    # Sanity: валидный relation_type проходит
    c = await _make_memory(owner, "факт C")
    d = await _make_memory(owner, "факт D")
    async with get_session() as session:
        owner3 = await get_or_create_user(session, OWNER_TG_ID)
        result = await link_memories(
            session,
            owner3,
            source_id=d.id,
            target_id=c.id,
            weight=0.95,
            relation_type="supersedes",
        )
        await session.commit()
        assert result is not None
        assert result.relation_type == "supersedes"


# ══════════════════════════════════════════════════════════════════════
# Phase 1.1: contradiction_detector uses return value of link_memories
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_contradiction_detector_logs_when_link_fails():
    """Fix #2: contradiction_detector должен логировать warning если link_memories
    вернул None (а не молча продолжать).

    В production link_memories может вернуть None если один из фактов не
    принадлежит пользователю. Без проверки мы никогда не узнаем, что
    supersedes-цепочка не создалась.
    """
    import logging
    from src.core.memory import contradiction_detector

    # Проверяем, что в коде handle_contradiction_response есть проверка
    # if link_result is not None: logger.debug(...) else: logger.warning(...)
    import inspect

    src = inspect.getsource(contradiction_detector)
    assert "if link_result is not None" in src, (
        "contradiction_detector must check return value of link_memories "
        "and log warning if it returns None"
    )
    assert "logger.warning" in src, (
        "contradiction_detector must use logger.warning (not info) for "
        "link failures to ensure they're visible in production logs"
    )
