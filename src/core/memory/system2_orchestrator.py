"""System2 Orchestrator — deep reasoning над графом памяти (Phase 3).

В отличие от System1 (быстрый recall по ключевым словам), System2 делает
медленное, обдуманное рассуждение:
- BFS-обход MemoryLink графа на большую глубину
- Поиск неочевидных связей между фактами
- Temporal reasoning через supersedes-цепочки
- Выявление скрытых паттернов и противоречий
- Генерация инсайтов о пользователе

Использование:
    from src.core.memory.system2_orchestrator import analyze

    analysis = await analyze(owner_id, query="кофе")
    for insight in analysis.insights:
        print(insight)

    path = await analyze(owner_id, focus_fact_id=42)
    for fact in path.found_facts:
        print(fact)
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.db.models import Memory, MemoryLink
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.core.memory.memory_chain import follow_supersedes_chain

logger = logging.getLogger(__name__)

# Максимальная глубина BFS в графе
MAX_BFS_DEPTH = 5
# Максимальное число узлов в обходе
MAX_NODES = 100


@dataclass
class GraphNode:
    """Узел в графе памяти."""

    memory_id: int
    fact: str
    depth: int = 0
    edge_from: int | None = None  # от какого memory_id пришли
    edge_type: str | None = None  # тип связи
    edge_weight: float = 0.0
    is_active: bool = True
    is_head: bool = False
    created_at: str = ""
    confidence: float = 0.0


@dataclass
class System2Analysis:
    """Результат глубокого анализа памяти."""

    user_id: int
    generated_at: str = ""

    # BFS-обход
    found_facts: list[GraphNode] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)

    # Цепочки эволюции
    evolution_chains: list[list[dict]] = field(default_factory=list)

    # Противоречия
    contradictions: list[dict] = field(default_factory=list)

    # Инсайты
    insights: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)

    # Статистика
    total_nodes_visited: int = 0
    max_depth_reached: int = 0

    def summary(self) -> str:
        """Краткая сводка анализа."""
        lines = [
            f"=== System2 Analysis (user #{self.user_id}) ===",
            f"Nodes visited: {self.total_nodes_visited}",
            f"Max depth: {self.max_depth_reached}",
            f"Facts found: {len(self.found_facts)}",
            f"Evolution chains: {len(self.evolution_chains)}",
            f"Contradictions: {len(self.contradictions)}",
            f"Insights: {len(self.insights)}",
            "",
        ]
        if self.insights:
            lines.append("Insights:")
            for i, insight in enumerate(self.insights, 1):
                lines.append(f"  {i}. {insight}")
            lines.append("")
        if self.found_facts:
            lines.append("Found facts (by depth):")
            for node in self.found_facts[:10]:
                active = "" if node.is_active else " [inactive]"
                head = " [HEAD]" if node.is_head else ""
                lines.append(
                    f"  d={node.depth} {node.edge_type or 'root'}"
                    f" -> {node.fact}{active}{head}"
                )
            if len(self.found_facts) > 10:
                lines.append(f"  ... ещё {len(self.found_facts) - 10}")
        return "\n".join(lines)


async def analyze(
    owner_id: int,
    *,
    query: str | None = None,
    focus_fact_id: int | None = None,
    max_depth: int = MAX_BFS_DEPTH,
    include_chains: bool = True,
    include_contradictions: bool = True,
) -> System2Analysis:
    """Глубокий анализ памяти пользователя.

    Args:
        owner_id: ID пользователя.
        query: Опциональный текстовый запрос для фильтрации стартовых узлов.
        focus_fact_id: Начать обход с конкретного факта.
        max_depth: Максимальная глубина BFS (по умолчанию 5).
        include_chains: Включать ли supersedes-цепочки.
        include_contradictions: Включать ли противоречия.

    Returns:
        System2Analysis с найденными фактами, связями и инсайтами.
    """
    analysis = System2Analysis(user_id=owner_id)
    analysis.generated_at = datetime.now(timezone.utc).isoformat()

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)

        # 1. Находим стартовые узлы
        start_ids: list[int] = []
        if focus_fact_id is not None:
            start_ids = [focus_fact_id]
        elif query:
            # Поиск по тексту в фактах
            result = await session.execute(
                select(Memory)
                .where(
                    Memory.user_id == owner.id,
                    Memory.is_active == True,
                    Memory.fact.ilike(f"%{query}%"),
                )
                .limit(10)
            )
            matches = result.scalars().all()
            start_ids = [m.id for m in matches]
        else:
            # Берём последние 5 активных фактов
            result = await session.execute(
                select(Memory)
                .where(
                    Memory.user_id == owner.id,
                    Memory.is_active == True,
                )
                .order_by(Memory.created_at.desc())
                .limit(5)
            )
            recent = result.scalars().all()
            start_ids = [m.id for m in recent]

        if not start_ids:
            logger.debug("System2: no start nodes found for user %d", owner_id)
            return analysis

        # 2. Загружаем индекс memory_id → Memory
        all_mem_ids = set(start_ids)
        mem_map: dict[int, Memory] = {}

        # Загружаем стартовые узлы
        for mid in start_ids:
            mem = await session.get(Memory, mid)
            if mem:
                mem_map[mid] = mem
                all_mem_ids.add(mid)

        # 3. BFS по MemoryLink графу
        visited: set[int] = set()
        queue: deque[tuple[int, int, str | None, float]] = deque()
        # (memory_id, depth, edge_type, edge_weight)

        for mid in start_ids:
            if mid in mem_map:
                queue.append((mid, 0, None, 0.0))
                visited.add(mid)

        bfs_nodes: list[GraphNode] = []
        all_links: list[dict] = []

        while queue and len(visited) < MAX_NODES:
            current_id, depth, edge_type, edge_weight = queue.popleft()

            if depth > max_depth:
                continue
            analysis.max_depth_reached = max(analysis.max_depth_reached, depth)

            mem = mem_map.get(current_id)
            if not mem:
                mem = await session.get(Memory, current_id)
                if mem:
                    mem_map[current_id] = mem

            if mem:
                node = GraphNode(
                    memory_id=current_id,
                    fact=mem.fact or f"mem#{current_id}",
                    depth=depth,
                    edge_from=None,
                    edge_type=edge_type,
                    edge_weight=edge_weight,
                    is_active=bool(mem.is_active),
                    created_at=mem.created_at.isoformat() if mem.created_at else "",
                    confidence=mem.confidence or 0.0,
                )
                bfs_nodes.append(node)

            if depth >= max_depth:
                continue

            # Находим все исходящие и входящие связи
            from sqlalchemy import or_

            links_result = await session.execute(
                select(MemoryLink)
                .where(
                    MemoryLink.user_id == owner.id,
                    or_(
                        MemoryLink.source_id == current_id,
                        MemoryLink.target_id == current_id,
                    ),
                )
                .limit(20)
            )
            links = links_result.scalars().all()

            for link in links:
                # Определяем направление
                if link.source_id == current_id:
                    neighbor_id = link.target_id
                    rel_type = link.relation_type or "related"
                else:
                    neighbor_id = link.source_id
                    # Инвертируем тип связи для обратного направления
                    rev_map = {
                        "cause": "effect",
                        "effect": "cause",
                        "supersedes": "superseded_by",
                        "contradicts": "contradicted_by",
                        "supports": "supported_by",
                        "continues": "continued_by",
                        "example_of": "example_of",
                        "co_temporal": "co_temporal",
                        "co_entity": "co_entity",
                        "preceded": "preceded",
                    }
                    rel_type = rev_map.get(
                        link.relation_type or "", link.relation_type or "related"
                    )

                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, depth + 1, rel_type, link.weight or 0.0))

                all_links.append(
                    {
                        "from": current_id,
                        "to": neighbor_id,
                        "type": rel_type,
                        "weight": link.weight or 0.0,
                    }
                )

        analysis.found_facts = bfs_nodes
        analysis.edges = all_links
        analysis.total_nodes_visited = len(visited)

        # 4. Генерируем инсайты из найденных фактов
        analysis = await _generate_insights(analysis, bfs_nodes, all_links)

        # 5. Supersedes-цепочки
        if include_chains:
            # Ищем supersedes-связи среди найденных узлов
            superseded_sources = {
                link["from"]
                for link in all_links
                if link["type"] in ("supersedes", "superseded_by")
            }
            for mid in superseded_sources:
                try:
                    chain = await follow_supersedes_chain(session, owner, mid)
                    if len(chain) > 1:
                        analysis.evolution_chains.append(chain)
                except Exception:
                    logger.debug("System2: chain failed for %d", mid, exc_info=True)

        # 6. Противоречия
        if include_contradictions:
            for link in all_links:
                if link["type"] in ("contradicts", "contradicted_by"):
                    analysis.contradictions.append(
                        {
                            "memory_a": link["from"],
                            "memory_b": link["to"],
                            "weight": link["weight"],
                        }
                    )

    return analysis


async def _generate_insights(
    analysis: System2Analysis,
    nodes: list[GraphNode],
    edges: list[dict],
) -> System2Analysis:
    """Генерирует текстовые инсайты на основе найденных фактов и связей."""

    # 1. Паттерн: изменение предпочтений (supersedes)
    supersedes_edges = [
        e for e in edges if e["type"] in ("supersedes", "superseded_by")
    ]
    if supersedes_edges:
        analysis.insights.append(
            f"Обнаружено {len(supersedes_edges)} supersedes-связей — "
            f"предпочтения или факты пользователя менялись со временем."
        )

    # 2. Паттерн: противоречия
    contradict_edges = [
        e for e in edges if e["type"] in ("contradicts", "contradicted_by")
    ]
    if contradict_edges:
        analysis.insights.append(
            f"Обнаружено {len(contradict_edges)} противоречий в памяти. "
            f"Стоит уточнить у пользователя."
        )

    # 3. Паттерн: глубина графа
    max_d = max((n.depth for n in nodes), default=0)
    if max_d >= 3:
        analysis.insights.append(
            f"Граф памяти глубокий (max depth={max_d}) — "
            f"много связанных фактов, хорошая структурированность."
        )

    # 4. Паттерн: много неактивных фактов
    inactive = [n for n in nodes if not n.is_active]
    if inactive and len(nodes) > 0:
        ratio = len(inactive) / len(nodes)
        if ratio > 0.3:
            analysis.insights.append(
                f"{len(inactive)} из {len(nodes)} найденных фактов неактивны "
                f"({ratio:.0%}) — возможно, много устаревшей информации."
            )

    # 5. Паттерн: ключевые узлы (хабы)
    in_degree: dict[int, int] = {}
    out_degree: dict[int, int] = {}
    for e in edges:
        out_degree[e["from"]] = out_degree.get(e["from"], 0) + 1
        in_degree[e["to"]] = in_degree.get(e["to"], 0) + 1

    hubs = [mid for mid, deg in out_degree.items() if deg >= 3]
    if hubs:
        analysis.insights.append(
            f"Найдено {len(hubs)} хаб-фактов (3+ связей) — "
            f"ключевые узлы в картине мира пользователя."
        )

    # 6. Паттерн: изолированные факты (без связей)
    connected = set()
    for e in edges:
        connected.add(e["from"])
        connected.add(e["to"])
    isolated = [n for n in nodes if n.memory_id not in connected and n.depth == 0]
    if isolated:
        analysis.patterns.append(
            f"{len(isolated)} изолированных фактов без связей — "
            f"возможно, стоит построить ассоциации."
        )

    # 7. Temporal pattern: факты сгруппированы по времени
    dates = [n.created_at for n in nodes if n.created_at]
    if len(dates) >= 3:
        analysis.patterns.append(
            f"Временной разброс: {len(dates)} фактов, "
            f"старейший — {min(dates)[:10]}, новейший — {max(dates)[:10]}."
        )

    return analysis
