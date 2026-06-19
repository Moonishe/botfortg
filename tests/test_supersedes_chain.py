"""Тесты для Hy-Memory evolution chains: supersedes-связи и follow_supersedes_chain()."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.db.session import get_session
from src.core.memory.memory_service import save_memory_single
from src.db.repo import get_or_create_user, link_memories
from src.core.memory.memory_chain import follow_supersedes_chain


OWNER_TG_ID = 99001


@pytest.fixture(autouse=True)
def setup_db():
    """Пересоздаёт таблицы перед каждым тестом (in-memory SQLite)."""
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
    """Создаёт Memory с управляемым created_at (смещение в секундах от now)."""
    async with get_session() as session:
        m = await save_memory_single(
            session,
            owner,
            fact=fact,
            source="chat",
            confidence=0.9,
        )
        if m is None:
            return None
        # Подкручиваем created_at чтобы цепочка была упорядочена
        new_ts = datetime.now(timezone.utc) - timedelta(seconds=created_offset_sec)
        m.created_at = new_ts
        await session.commit()
        return m


# ──────────────────────────────────────────────────────────────────────
# follow_supersedes_chain
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_link_chain():
    """A→B (B supersedes A): цепочка из 2 фактов, head=B."""
    owner = await _make_owner()
    a = await _make_memory(owner, "работаю в Яндексе", created_offset_sec=120)
    b = await _make_memory(owner, "работаю в Google", created_offset_sec=0)

    async with get_session() as session:
        await link_memories(
            session,
            owner,
            source_id=b.id,
            target_id=a.id,
            weight=0.95,
            relation_type="supersedes",
        )
        await session.commit()

        chain_from_a = await follow_supersedes_chain(session, owner, a.id)
        chain_from_b = await follow_supersedes_chain(session, owner, b.id)

    # Из любой точки попадаем в обе записи
    assert len(chain_from_a) == 2
    assert len(chain_from_b) == 2

    # Хронологический порядок: A (старше) → B (новее)
    assert chain_from_a[0]["memory_id"] == a.id
    assert chain_from_a[1]["memory_id"] == b.id
    assert chain_from_a[1]["is_head"] is True
    assert chain_from_a[0]["is_head"] is False
    assert chain_from_a[0]["fact"] == "работаю в Яндексе"
    assert chain_from_a[1]["fact"] == "работаю в Google"


@pytest.mark.asyncio
async def test_three_node_chain():
    """A→B→C (C supersedes B, B supersedes A): цепочка из 3 фактов, head=C."""
    owner = await _make_owner()
    a = await _make_memory(owner, "живу в Москве", created_offset_sec=300)
    b = await _make_memory(owner, "переехал в Питер", created_offset_sec=120)
    c = await _make_memory(owner, "переехал в Казань", created_offset_sec=0)

    async with get_session() as session:
        await link_memories(
            session,
            owner,
            source_id=b.id,
            target_id=a.id,
            weight=0.95,
            relation_type="supersedes",
        )
        await link_memories(
            session,
            owner,
            source_id=c.id,
            target_id=b.id,
            weight=0.95,
            relation_type="supersedes",
        )
        await session.commit()

        chain = await follow_supersedes_chain(session, owner, a.id)

    assert len(chain) == 3
    assert [c_["memory_id"] for c_ in chain] == [a.id, b.id, c.id]
    # Только head помечен is_head=True
    head_flags = [c_["is_head"] for c_ in chain]
    assert sum(head_flags) == 1
    assert chain[-1]["is_head"] is True
    assert chain[-1]["memory_id"] == c.id


@pytest.mark.asyncio
async def test_cycle_does_not_infinite_loop():
    """A→B→A (цикл): должен вернуть оба факта и не зациклиться."""
    owner = await _make_owner()
    a = await _make_memory(owner, "факт A", created_offset_sec=60)
    b = await _make_memory(owner, "факт B", created_offset_sec=0)

    async with get_session() as session:
        # Создаём цикл: A↔B через supersedes
        await link_memories(
            session,
            owner,
            source_id=b.id,
            target_id=a.id,
            weight=0.9,
            relation_type="supersedes",
        )
        # link_memories уже создаёт обратную связь, так что цикл готов.
        # Создадим ещё одну связь A→B для имитации настоящего цикла —
        # попытка создать прямую связь обновит существующую reverse (no-op).
        # Для гарантированного цикла добавим вторую пару через чистый
        # MemoryLink, обходя link_memories.
        from src.db.models import MemoryLink

        link_a_to_b = MemoryLink(
            user_id=owner.id,
            source_id=a.id,
            target_id=b.id,
            weight=0.9,
            relation_type="supersedes",
        )
        link_b_to_a = MemoryLink(
            user_id=owner.id,
            source_id=b.id,
            target_id=a.id,
            weight=0.9,
            relation_type="supersedes",
        )
        # Проверим, что обратные не дубль — должны отсутствовать, чтобы
        # не нарваться на UniqueConstraint
        from sqlalchemy import select

        existing_ab = await session.execute(
            select(MemoryLink).where(
                MemoryLink.user_id == owner.id,
                MemoryLink.source_id == a.id,
                MemoryLink.target_id == b.id,
            )
        )
        if not existing_ab.scalar_one_or_none():
            session.add(link_a_to_b)
        existing_ba = await session.execute(
            select(MemoryLink).where(
                MemoryLink.user_id == owner.id,
                MemoryLink.source_id == b.id,
                MemoryLink.target_id == a.id,
            )
        )
        if not existing_ba.scalar_one_or_none():
            session.add(link_b_to_a)
        await session.commit()

        # Если тест зациклится — pytest упадёт по timeout, что и есть
        # ожидаемое поведение. Здесь же мы просто проверяем, что мы
        # получили результат и оба факта в нём.
        chain = await follow_supersedes_chain(session, owner, a.id, max_depth=10)

    # В цепочке должно быть ровно 2 уникальных факта (A и B).
    # Без защиты от циклов получили бы бесконечный BFS.
    ids = {c_["memory_id"] for c_ in chain}
    assert ids == {a.id, b.id}
    assert len(chain) == 2


@pytest.mark.asyncio
async def test_no_supersedes_edges_returns_self_only():
    """Без supersedes-рёбер цепочка содержит только начальный факт."""
    owner = await _make_owner()
    a = await _make_memory(owner, "изолированный факт A")
    b = await _make_memory(owner, "изолированный факт B")

    # Свяжем факты через cause (НЕ supersedes) — цепочка supersedes
    # не должна их подобрать.
    async with get_session() as session:
        await link_memories(
            session,
            owner,
            source_id=b.id,
            target_id=a.id,
            weight=0.5,
            relation_type="cause",
        )
        await session.commit()

        chain = await follow_supersedes_chain(session, owner, a.id)

    # Только сам A, без B
    assert len(chain) == 1
    assert chain[0]["memory_id"] == a.id
    assert chain[0]["is_head"] is True  # единственный — голова


@pytest.mark.asyncio
async def test_empty_when_memory_missing():
    """Несуществующий memory_id → пустой список."""
    owner = await _make_owner()
    async with get_session() as session:
        chain = await follow_supersedes_chain(session, owner, 99999)

    assert chain == []


@pytest.mark.asyncio
async def test_is_head_only_on_newest_fact():
    """is_head=True ровно на одном факте — самом новом в цепочке."""
    owner = await _make_owner()
    a = await _make_memory(owner, "версия 1", created_offset_sec=200)
    b = await _make_memory(owner, "версия 2", created_offset_sec=100)
    c = await _make_memory(owner, "версия 3", created_offset_sec=0)

    async with get_session() as session:
        await link_memories(
            session,
            owner,
            source_id=b.id,
            target_id=a.id,
            weight=0.95,
            relation_type="supersedes",
        )
        await link_memories(
            session,
            owner,
            source_id=c.id,
            target_id=b.id,
            weight=0.95,
            relation_type="supersedes",
        )
        await session.commit()

        chain = await follow_supersedes_chain(session, owner, b.id)

    head_count = sum(1 for c_ in chain if c_["is_head"])
    assert head_count == 1
    head = next(c_ for c_ in chain if c_["is_head"])
    assert head["memory_id"] == c.id
    assert head["fact"] == "версия 3"


@pytest.mark.asyncio
async def test_max_depth_respected():
    """max_depth ограничивает размер цепочки."""
    owner = await _make_owner()

    # Создаём 5 фактов, связанных цепочкой: 1→2→3→4→5
    mems = []
    for i in range(5):
        m = await _make_memory(owner, f"факт {i}", created_offset_sec=(4 - i) * 10)
        mems.append(m)

    async with get_session() as session:
        for i in range(1, 5):
            await link_memories(
                session,
                owner,
                source_id=mems[i].id,
                target_id=mems[i - 1].id,
                weight=0.95,
                relation_type="supersedes",
            )
        await session.commit()

        chain = await follow_supersedes_chain(session, owner, mems[0].id, max_depth=3)

    assert len(chain) == 3


# ──────────────────────────────────────────────────────────────────────
# contradiction_detector — supersedes link
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_contradiction_confirm_creates_supersedes_link():
    """Подтверждение противоречия создаёт supersedes-ссылку new→old."""
    from src.core.memory.contradiction_detector import (
        detect_contradiction,
        store_pending_contradiction,
        check_contradiction_response,
    )

    owner = await _make_owner()
    old = await _make_memory(owner, "я пью кофе", created_offset_sec=600)
    # Создаём «новый» факт — будто только что извлекли из диалога
    new = await _make_memory(owner, "я не пью кофе", created_offset_sec=10)

    # Эмулируем flow: detect → store → confirm
    contradiction = {
        "contradicted_fact": old.fact,
        "memory_id": old.id,
        "confidence": 0.8,
        "suggestion": "Ты говорил что я пью кофе. Передумал?",
    }
    await store_pending_contradiction(OWNER_TG_ID, contradiction)

    response = await check_contradiction_response(OWNER_TG_ID, "да")
    assert response is not None
    assert "Понял" in response

    # Проверяем что link создан
    from src.db.models import MemoryLink
    from sqlalchemy import select

    async with get_session() as session:
        result = await session.execute(
            select(MemoryLink).where(
                MemoryLink.user_id == owner.id,
                MemoryLink.relation_type == "supersedes",
            )
        )
        links = result.scalars().all()

    # link_memories создаёт 2 направления (forward + reverse)
    src_ids = {lk.source_id for lk in links}
    tgt_ids = {lk.target_id for lk in links}
    # new.id должен быть source в одном из направлений
    assert (new.id in src_ids) or (new.id in tgt_ids)
    assert (old.id in src_ids) or (old.id in tgt_ids)


@pytest.mark.asyncio
async def test_contradiction_confirm_fallback_when_no_new_memory():
    """Если нового факта в последние 5 минут нет — fallback на is_active=False без ссылки."""
    from src.core.memory.contradiction_detector import (
        store_pending_contradiction,
        check_contradiction_response,
    )

    owner = await _make_owner()
    # Только старый факт, нового нет вообще
    old = await _make_memory(owner, "я пью кофе", created_offset_sec=3600)

    contradiction = {
        "contradicted_fact": old.fact,
        "memory_id": old.id,
        "confidence": 0.8,
        "suggestion": "Ты говорил что я пью кофе. Передумал?",
    }
    await store_pending_contradiction(OWNER_TG_ID, contradiction)

    response = await check_contradiction_response(OWNER_TG_ID, "да")
    assert response is not None

    # Старый факт помечен неактивным (fallback)
    from src.db.models import Memory
    from sqlalchemy import select

    async with get_session() as session:
        m = (
            await session.execute(select(Memory).where(Memory.id == old.id))
        ).scalar_one()
        assert m.is_active is False
        assert m.sentiment == "contradictory"

        # Но supersedes-связи не должно быть
        from src.db.models import MemoryLink as _ML

        sup_links = (
            (
                await session.execute(
                    select(_ML).where(
                        _ML.user_id == owner.id,
                        _ML.relation_type == "supersedes",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(sup_links) == 0
