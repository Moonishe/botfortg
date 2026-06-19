"""Unit and integration tests for Knowledge Graph BFS traversal.

Covers:
  - traverse() — empty graph (no entities)
  - traverse() — entity not found
  - traverse() — single node (no relations)
  - traverse() — two connected nodes
  - traverse() — max hops limiting
  - traverse() — hops clamping to valid range
  - traverse() — invalid user (non-existent)
  - traverse() — bidirectional relations
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import init_db, get_session, engine, Base
from src.db.models._memory import Entity, EntityRelation
from src.db.repo import get_or_create_user
from src.core.memory.graph_traversal import traverse, MAX_HOPS, MAX_NODES

OWNER_TG_ID = 123456789


@pytest.fixture(autouse=True)
def setup_db():
    """Пересоздаёт таблицы перед каждым тестом."""

    async def _recreate():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        # FTS5 tables
        await init_db()

    asyncio.run(_recreate())


async def _create_entity(
    session: "AsyncSession",
    user_id: int,
    name: str,
    etype: str = "person",
) -> Entity:
    """Helper: создать entity для теста."""
    entity = Entity(
        user_id=user_id,
        name=name,
        type=etype,
    )
    session.add(entity)
    await session.flush()
    return entity


async def _create_relation(
    session: AsyncSession,
    user_id: int,
    source_id: int,
    target_id: int,
    relation: str = "related_to",
    weight: float = 1.0,
) -> EntityRelation:
    """Helper: создать связь между сущностями."""
    rel = EntityRelation(
        user_id=user_id,
        source_id=source_id,
        target_id=target_id,
        relation=relation,
        weight=weight,
    )
    session.add(rel)
    await session.flush()
    return rel


# ────────────────────────────────────────────────────────────────────
# Empty / not found
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_traverse_empty_graph():
    """traverse() для пустого графа — ok, нет узлов."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
    result = await traverse(OWNER_TG_ID, "anything")
    assert result["ok"] is True
    assert result["start_entity"] is None
    assert result["total_nodes"] == 0
    assert result["total_edges"] == 0
    assert "не найдена" in result.get("message", "")


@pytest.mark.asyncio
async def test_traverse_entity_not_found():
    """traverse() для несуществующей сущности — message, пустой граф."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        await _create_entity(session, owner.id, "Alice")
        await session.commit()

    result = await traverse(OWNER_TG_ID, "Bob")
    assert result["ok"] is True
    assert result["start_entity"] is None
    assert result["total_nodes"] == 0
    assert "не найдена" in result.get("message", "")


# ────────────────────────────────────────────────────────────────────
# Single node / two nodes
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_traverse_single_node_no_relations():
    """traverse() с одной сущностью без связей — 1 узел, 0 рёбер."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        await _create_entity(session, owner.id, "Alice")
        await session.commit()

    result = await traverse(OWNER_TG_ID, "Alice")
    assert result["total_nodes"] == 1
    assert result["total_edges"] == 0
    assert result["start_entity"]["name"] == "Alice"
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["name"] == "Alice"


@pytest.mark.asyncio
async def test_traverse_two_connected_nodes():
    """traverse() находит связные узлы через BFS."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        alice = await _create_entity(session, owner.id, "Alice")
        bob = await _create_entity(session, owner.id, "Bob")
        await _create_relation(session, owner.id, alice.id, bob.id, "friend")
        await session.commit()

    result = await traverse(OWNER_TG_ID, "Alice", hops=2)
    assert result["total_nodes"] == 2
    assert result["total_edges"] > 0
    names = {n["name"] for n in result["nodes"]}
    assert "Alice" in names
    assert "Bob" in names


@pytest.mark.asyncio
async def test_traverse_case_insensitive_like():
    """traverse() использует ilike — поиск нечувствителен к регистру."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        await _create_entity(session, owner.id, "AliceCooper")
        await session.commit()

    # LIKE '%alice%' через ilike
    result = await traverse(OWNER_TG_ID, "alice")
    assert result["total_nodes"] == 1
    assert result["start_entity"]["name"] == "AliceCooper"


# ────────────────────────────────────────────────────────────────────
# Hops limiting
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_traverse_hops_limit():
    """BFS не выходит за пределы max_hops."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        a = await _create_entity(session, owner.id, "A")
        b = await _create_entity(session, owner.id, "B")
        c = await _create_entity(session, owner.id, "C")
        d = await _create_entity(session, owner.id, "D")
        # A → B → C → D (цепочка)
        await _create_relation(session, owner.id, a.id, b.id, "next")
        await _create_relation(session, owner.id, b.id, c.id, "next")
        await _create_relation(session, owner.id, c.id, d.id, "next")
        await session.commit()

    # hops=1 — A + соседи (BFS от A с 0 шагов вовне = только сам A, но
    # текущая реализация добавляет соседей на dist=0 → neighbours на dist=1
    # попадают в visited до проверки dist>=hops)
    result = await traverse(OWNER_TG_ID, "A", hops=1)
    assert result["total_nodes"] == 2  # A + B
    names_1 = {n["name"] for n in result["nodes"]}
    assert "A" in names_1
    assert "B" in names_1

    # hops=2 — A + B + C (2 шага от A)
    result = await traverse(OWNER_TG_ID, "A", hops=2)
    names_2 = {n["name"] for n in result["nodes"]}
    assert "A" in names_2
    assert "B" in names_2
    assert "C" in names_2
    assert "D" not in names_2

    # hops=3 — A + B + C + D
    result = await traverse(OWNER_TG_ID, "A", hops=3)
    names_3 = {n["name"] for n in result["nodes"]}
    assert "A" in names_3
    assert "B" in names_3
    assert "C" in names_3
    assert "D" in names_3


@pytest.mark.asyncio
async def test_traverse_hops_clamping():
    """hops выходит за MAX_HOPS → обрезается до MAX_HOPS.
    hops < 1 → становится 1.
    """
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        # Создаём цепочку длиной MAX_HOPS + 2
        entities = []
        for i in range(MAX_HOPS + 3):
            e = await _create_entity(session, owner.id, f"Node{i}")
            entities.append(e)
        for i in range(len(entities) - 1):
            await _create_relation(
                session, owner.id, entities[i].id, entities[i + 1].id, "chain"
            )
        await session.commit()

    # Не должно упасть, обрежется до MAX_HOPS
    result = await traverse(OWNER_TG_ID, "Node0", hops=MAX_HOPS + 10)
    assert result["total_nodes"] <= MAX_HOPS + 1  # start + MAX_HOPS уровней

    # hops=0 → 1 → start + immediate neighbors
    result = await traverse(OWNER_TG_ID, "Node0", hops=0)
    assert result["total_nodes"] == 2  # Node0 + Node1

    # hops=-5 → 1 → start + immediate neighbors
    result = await traverse(OWNER_TG_ID, "Node0", hops=-5)
    assert result["total_nodes"] == 2  # Node0 + Node1


# ────────────────────────────────────────────────────────────────────
# Bidirectional relations (граф ненаправленный)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_traverse_bidirectional():
    """Связь A→B позволяет обойти граф в обе стороны."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        alice = await _create_entity(session, owner.id, "Alice")
        bob = await _create_entity(session, owner.id, "Bob")
        await _create_relation(session, owner.id, alice.id, bob.id, "friend")
        await session.commit()

    # От Alice → Bob
    result_a = await traverse(OWNER_TG_ID, "Alice", hops=2)
    names_a = {n["name"] for n in result_a["nodes"]}
    assert "Bob" in names_a

    # От Bob → Alice (обратное ребро rev_friend)
    result_b = await traverse(OWNER_TG_ID, "Bob", hops=2)
    names_b = {n["name"] for n in result_b["nodes"]}
    assert "Alice" in names_b


# ────────────────────────────────────────────────────────────────────
# Edge cases
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_traverse_max_nodes_cap():
    """BFS останавливается при достижении MAX_NODES (50)."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        # Создаём 60 сущностей
        entities = []
        for i in range(60):
            e = await _create_entity(session, owner.id, f"Star{i}")
            entities.append(e)
        # Соединяем все со всеми (звезда от Star0)
        star0 = entities[0]
        for e in entities[1:]:
            await _create_relation(session, owner.id, star0.id, e.id, "orbit")
        await session.commit()

    result = await traverse(OWNER_TG_ID, "Star0", hops=2)
    assert result["total_nodes"] <= MAX_NODES


@pytest.mark.asyncio
async def test_traverse_multiple_matches_picks_first():
    """Если ilike находит несколько сущностей — берётся первая (по created_at desc)."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        # Обе содержат "Alex"
        await _create_entity(session, owner.id, "Alexandra")
        await _create_entity(session, owner.id, "Alexander")
        await session.commit()

    result = await traverse(OWNER_TG_ID, "Alex")
    assert result["total_nodes"] == 1
    # Alexander создан позже → он первый в ORDER BY created_at DESC
    assert result["start_entity"]["name"] == "Alexander"


@pytest.mark.asyncio
async def test_traverse_relation_weight():
    """Вес связи попадает в результат edges."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        a = await _create_entity(session, owner.id, "Alpha")
        b = await _create_entity(session, owner.id, "Beta")
        await _create_relation(session, owner.id, a.id, b.id, "depends_on", weight=0.75)
        await session.commit()

    result = await traverse(OWNER_TG_ID, "Alpha", hops=2)
    edges = result["edges"]
    assert len(edges) >= 1
    # Проверяем что есть ребро с нужным отношением
    found = any(
        e["relation"] in ("depends_on", "rev_depends_on") and e["weight"] == 0.75
        for e in edges
    )
    assert found, f"Relation with weight 0.75 not found in {edges}"
