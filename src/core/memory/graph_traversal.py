"""Обход графа знаний — BFS от заданной сущности.

Переиспользует BFS-паттерн из evolution_chain.py.
Строит adjacency list из EntityRelation, обходит граф с ограничением по hops.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select

from src.db.models._memory import Entity, EntityRelation
from src.db.repo import get_or_create_user
from src.db.session import get_session

logger = logging.getLogger(__name__)

# Максимальная глубина BFS-обхода
MAX_HOPS = 5
# Максимальное число узлов в результате
MAX_NODES = 50


async def traverse(
    user_id: int,
    start_entity: str,
    hops: int = 2,
) -> dict:
    """BFS-обход графа знаний от start_entity с ограничением по hops.

    Алгоритм:
    1. Найти Entity по имени (LIKE-поиск, case-insensitive).
    2. Загрузить все EntityRelation для пользователя.
    3. Построить adjacency list.
    4. BFS от стартовой сущности с max_hops.
    5. Вернуть связный подграф.

    Args:
        user_id: Telegram ID пользователя.
        start_entity: Имя сущности для старта обхода (подстрока).
        hops: Максимальное число шагов (по умолчанию 2).

    Returns:
        dict с ключами:
          - start_entity: dict — найденная стартовая сущность
          - nodes: list[dict] — все узлы графа
          - edges: list[dict] — все рёбра графа
          - total_nodes: int
          - total_edges: int
          - generated_at: str — ISO timestamp
    """
    hops = max(1, min(hops, MAX_HOPS))

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, user_id)

            # 1. Найти стартовую сущность (LIKE-поиск)
            start_result = await session.execute(
                select(Entity).where(
                    and_(
                        Entity.user_id == owner.id,
                        Entity.name.ilike(f"%{start_entity}%"),
                    )
                )
            )
            start_entities = start_result.scalars().all()

            if not start_entities:
                return {
                    "ok": True,
                    "start_entity": None,
                    "nodes": [],
                    "edges": [],
                    "total_nodes": 0,
                    "total_edges": 0,
                    "message": f"Сущность '{start_entity}' не найдена в графе знаний.",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }

            # Берём первую найденную как стартовую
            start = start_entities[0]
            start_id = start.id

            # 2. Загружаем все связи пользователя
            rel_result = await session.execute(
                select(EntityRelation).where(
                    EntityRelation.user_id == owner.id,
                )
            )
            all_relations = rel_result.scalars().all()

            # 3. Строим adjacency list (ненаправленный граф)
            adjacency: dict[int, set[tuple[int, str, float]]] = {}
            # adjacency[node_id] = {(neighbor_id, relation, weight), ...}

            for rel in all_relations:
                if rel.source_id not in adjacency:
                    adjacency[rel.source_id] = set()
                if rel.target_id not in adjacency:
                    adjacency[rel.target_id] = set()

                adjacency[rel.source_id].add(
                    (rel.target_id, rel.relation, rel.weight or 1.0)
                )
                # Обратное ребро для ненаправленного обхода
                adjacency[rel.target_id].add(
                    (rel.source_id, f"rev_{rel.relation}", rel.weight or 1.0)
                )

            # 4. BFS
            visited: set[int] = set()
            distance: dict[int, int] = {}  # node_id → distance
            edges_found: list[dict] = []  # рёбра подграфа

            queue: deque[tuple[int, int]] = deque()
            queue.append((start_id, 0))
            visited.add(start_id)
            distance[start_id] = 0

            while queue and len(visited) < MAX_NODES:
                current_id, dist = queue.popleft()

                if dist >= hops:
                    continue

                neighbors = adjacency.get(current_id, set())
                for neighbor_id, relation, weight in neighbors:
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        distance[neighbor_id] = dist + 1
                        queue.append((neighbor_id, dist + 1))

                    # Добавляем ребро (только если оба узла в visited)
                    if neighbor_id in visited and current_id in visited:
                        edges_found.append(
                            {
                                "source_id": current_id,
                                "target_id": neighbor_id,
                                "relation": relation,
                                "weight": weight,
                            }
                        )

            # 5. Загружаем все найденные узлы
            node_ids = list(visited)
            nodes_result = await session.execute(
                select(Entity).where(Entity.id.in_(node_ids))
            )
            entity_map: dict[int, Entity] = {
                e.id: e for e in nodes_result.scalars().all()
            }

            # Формируем результат
            nodes_list: list[dict[str, Any]] = []
            for nid in node_ids:
                entity = entity_map.get(nid)
                if entity is None:
                    continue
                nodes_list.append(
                    {
                        "id": entity.id,
                        "name": entity.name,
                        "type": entity.type,
                        "metadata_json": entity.metadata_json,
                        "distance": distance.get(nid, -1),
                        "is_start": nid == start_id,
                    }
                )

            # Дедуплицируем рёбра и резолвим имена
            edge_set: set[tuple[int, int, str]] = set()
            unique_edges: list[dict[str, Any]] = []
            for edge in edges_found:
                key = (edge["source_id"], edge["target_id"], edge["relation"])
                if key in edge_set:
                    continue
                edge_set.add(key)
                src_name = (
                    entity_map[edge["source_id"]].name
                    if edge["source_id"] in entity_map
                    else "?"
                )
                tgt_name = (
                    entity_map[edge["target_id"]].name
                    if edge["target_id"] in entity_map
                    else "?"
                )
                unique_edges.append(
                    {
                        "source": src_name,
                        "target": tgt_name,
                        "relation": edge["relation"],
                        "weight": edge["weight"],
                    }
                )

            return {
                "ok": True,
                "start_entity": {
                    "id": start.id,
                    "name": start.name,
                    "type": start.type,
                },
                "nodes": nodes_list[:MAX_NODES],
                "edges": unique_edges,
                "total_nodes": len(nodes_list),
                "total_edges": len(unique_edges),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

    except Exception:
        logger.debug(
            "Graph traversal failed for user %d, start='%s'",
            user_id,
            start_entity,
            exc_info=True,
        )
        return {
            "ok": False,
            "error": "Ошибка обхода графа знаний",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


async def search_entity(
    user_id: int,
    query: str,
    entity_type: str | None = None,
    limit: int = 20,
) -> dict:
    """Поиск сущностей по имени (LIKE-поиск).

    Args:
        user_id: Telegram ID пользователя.
        query: Поисковый запрос (подстрока).
        entity_type: Опциональный фильтр по типу (person, project, place, company, topic).
        limit: Максимальное число результатов.

    Returns:
        dict с ключами ok, entities, total, query.
    """
    if not query or not query.strip():
        return {"ok": True, "entities": [], "total": 0, "query": query}

    query = query.strip()[:128]
    limit = max(1, min(limit, 100))

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, user_id)

            conditions = [
                Entity.user_id == owner.id,
                Entity.name.ilike(f"%{query}%"),
            ]
            if entity_type:
                conditions.append(Entity.type == entity_type.strip()[:32])

            result = await session.execute(
                select(Entity)
                .where(and_(*conditions))
                .order_by(Entity.name)
                .limit(limit)
            )
            entities = result.scalars().all()

            entities_list: list[dict[str, Any]] = []
            for e in entities:
                # Загружаем связи сущности
                rel_count_result = await session.execute(
                    select(EntityRelation).where(
                        and_(
                            EntityRelation.user_id == owner.id,
                            or_(
                                EntityRelation.source_id == e.id,
                                EntityRelation.target_id == e.id,
                            ),
                        )
                    )
                )
                relations = rel_count_result.scalars().all()

                entities_list.append(
                    {
                        "id": e.id,
                        "name": e.name,
                        "type": e.type,
                        "metadata_json": e.metadata_json,
                        "relation_count": len(relations),
                        "created_at": e.created_at.isoformat()
                        if e.created_at
                        else None,
                    }
                )

            return {
                "ok": True,
                "entities": entities_list,
                "total": len(entities_list),
                "query": query,
            }

    except Exception:
        logger.debug(
            "Entity search failed for user %d, query='%s'",
            user_id,
            query,
            exc_info=True,
        )
        return {
            "ok": False,
            "error": "Ошибка поиска сущностей",
            "entities": [],
            "total": 0,
            "query": query,
        }


async def get_entity_by_name(
    user_id: int,
    name: str,
) -> dict:
    """Точный поиск одной сущности по имени.

    Args:
        user_id: Telegram ID пользователя.
        name: Точное имя сущности.

    Returns:
        dict с ключами ok, entity (или None).
    """
    if not name or not name.strip():
        return {"ok": True, "entity": None}

    name = name.strip()[:128]

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, user_id)

            result = await session.execute(
                select(Entity).where(
                    and_(
                        Entity.user_id == owner.id,
                        Entity.name == name,
                    )
                )
            )
            entity = result.scalar_one_or_none()

            if entity is None:
                return {"ok": True, "entity": None}

            return {
                "ok": True,
                "entity": {
                    "id": entity.id,
                    "name": entity.name,
                    "type": entity.type,
                    "metadata_json": entity.metadata_json,
                },
            }

    except Exception:
        logger.debug(
            "get_entity_by_name failed for user %d, name='%s'",
            user_id,
            name,
            exc_info=True,
        )
        return {"ok": False, "error": "Ошибка поиска сущности", "entity": None}
