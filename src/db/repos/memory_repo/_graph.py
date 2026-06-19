"""Memory repository — memory graph and auto-linking."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from sqlalchemy import and_, func, or_, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Memory, MemoryLink, User

logger = logging.getLogger(__name__)


async def link_memories(
    session: AsyncSession,
    user: User,
    source_id: int,
    target_id: int,
    weight: float = 0.5,
    relation_type: str | None = None,
) -> MemoryLink | None:
    """Создать/обновить связь между фактами памяти (many-to-many)."""

    # Lazy import to avoid circular: _graph -> _core
    from src.db.repos.memory_repo._core import _VALID_RELATION_TYPES

    # Валидация relation_type: LLM может галлюцинировать значения вроде
    # «supersede» (без 's') или «replaces». Такие значения молча попадут в БД
    # и не будут найдены ни одним relation-фильтром. Приводим к None вместо
    # райза — вызывающий код не должен падать из-за LLM-ошибки.
    if relation_type is not None and relation_type not in _VALID_RELATION_TYPES:
        logger.warning(
            "Invalid relation_type=%r for link %d->%d, dropping relation",
            relation_type,
            source_id,
            target_id,
        )
        relation_type = None

    # Проверить что оба факта принадлежат пользователю
    result = await session.execute(
        select(Memory).where(
            Memory.id.in_([source_id, target_id]), Memory.user_id == user.id
        )
    )
    if len(result.scalars().all()) < 2:
        return None  # один из фактов не найден или чужой

    # Проверить существующую связь
    existing = await session.execute(
        select(MemoryLink).where(
            MemoryLink.user_id == user.id,
            MemoryLink.source_id == source_id,
            MemoryLink.target_id == target_id,
        )
    )
    existing = existing.scalar_one_or_none()
    if existing:
        existing.weight = weight
        if relation_type:
            existing.relation_type = relation_type
        await session.flush()
        return existing

    # Создать новую + обратную
    link = MemoryLink(
        user_id=user.id,
        source_id=source_id,
        target_id=target_id,
        weight=weight,
        relation_type=relation_type,
    )
    session.add(link)

    # Обратная связь (если не дубль)
    # M7: обратная связь использует тот же relation_type что и прямая.
    # Семантически это неверно (relation_type описывает направление:
    # A supersedes B ≠ B supersedes A), но большинство downstream-кода
    # (memory_graph, memory_chain) работает с bidirectional edges и не
    # различает направление. Полноценный фикс потребовал бы reverse-
    # relation_type mapping (supersedes→superseded_by, cause→effect),
    # что — breaking change для memory_graph BFS.
    # Пока оставляем как есть — tradeoff осознанный.
    reverse_check = await session.execute(
        select(MemoryLink).where(
            MemoryLink.user_id == user.id,
            MemoryLink.source_id == target_id,
            MemoryLink.target_id == source_id,
        )
    )
    if not reverse_check.scalar_one_or_none():
        rev = MemoryLink(
            user_id=user.id,
            source_id=target_id,
            target_id=source_id,
            weight=weight,
            relation_type=relation_type,
        )
        session.add(rev)

    await session.flush()
    return link


async def unlink_memories(
    session: AsyncSession, user: User, source_id: int, target_id: int
) -> None:
    """Удалить связь между фактами (в обе стороны)."""
    from sqlalchemy import delete as sa_delete

    await session.execute(
        sa_delete(MemoryLink).where(
            MemoryLink.user_id == user.id,
            or_(
                and_(
                    MemoryLink.source_id == source_id,
                    MemoryLink.target_id == target_id,
                ),
                and_(
                    MemoryLink.source_id == target_id,
                    MemoryLink.target_id == source_id,
                ),
            ),
        )
    )
    await session.flush()


async def list_memory_links(
    session: AsyncSession, user: User, limit: int = 500
) -> list[dict]:
    """Return all memory links for *user* as plain dicts.

    Used by graph export handlers. Limit is clamped to [1, 2000].
    """
    limit = max(1, min(limit, 2000))
    result = await session.execute(
        select(
            MemoryLink.source_id,
            MemoryLink.target_id,
            MemoryLink.weight,
            MemoryLink.relation_type,
        )
        .where(MemoryLink.user_id == user.id)
        .limit(limit)
    )
    return [
        {
            "source_id": int(r.source_id),
            "target_id": int(r.target_id),
            "weight": float(r.weight),
            "relation_type": r.relation_type,
        }
        for r in result.all()
    ]


async def get_linked_memories(
    session: AsyncSession, user: User, memory_id: int, limit: int = 10
) -> list[dict]:
    """Получить связанные факты с весами."""
    # Guard against invalid limit values (0 or negative → no results / invalid SQL)
    limit = max(1, limit)
    result = await session.execute(
        select(Memory, MemoryLink.weight, MemoryLink.relation_type)
        .join(MemoryLink, MemoryLink.target_id == Memory.id)
        .where(
            MemoryLink.source_id == memory_id,
            MemoryLink.user_id == user.id,
            Memory.is_active,
        )
        .order_by(MemoryLink.weight.desc())
        .limit(limit)
    )
    rows = result.all()
    linked: list[dict] = []
    for mem, weight, rel_type in rows:
        linked.append({"memory": mem, "weight": weight, "relation_type": rel_type})
    return linked


async def get_memory_graph(
    session: AsyncSession,
    user: User,
    memory_id: int,
    max_depth: int = 3,
    max_nodes: int = 20,
    max_branch: int = 10,
) -> list[dict]:
    """Строит граф связанных фактов BFS от memory_id.

    Каждый узел разбираётся в отдельном запросе, чтобы не загружать
    тысячи связей целиком.  Посещено не более ``max_nodes`` узлов,
    ветвление не более ``max_branch`` на узел.
    """
    # ── Input validation: guard against degenerated parameters ───────
    # max_nodes=0 or negative means "no nodes" — return empty.
    # max_branch<=0: SQLite LIMIT 0 returns nothing, LIMIT -1 returns ALL
    # rows (bypassing the limit). Clamp to safe range.
    # max_depth<0: root processed (depth=0 > -1? No), but no expansion
    # (0 < -1? No) → empty graph. Clamp.
    if max_nodes <= 0 or max_depth < 0:
        return []
    max_branch = max(1, max_branch)

    visited: set[int] = set()
    graph: list[dict] = []
    queue: deque[tuple[int, int]] = deque([(memory_id, 0)])

    while queue and len(visited) < max_nodes:
        mid, depth = queue.popleft()
        if mid in visited or depth > max_depth:
            continue
        visited.add(mid)
        if depth > 0:  # не добавляем корневой узел в граф, только соседей
            graph.append({"memory_id": mid, "depth": depth})
        if depth < max_depth:
            rows = (
                await session.execute(
                    select(
                        MemoryLink.target_id,
                        MemoryLink.weight,
                        MemoryLink.relation_type,
                    )
                    .where(
                        MemoryLink.source_id == mid,
                        MemoryLink.user_id == user.id,
                    )
                    .order_by(MemoryLink.weight.desc())
                    .limit(max_branch)
                )
            ).all()
            for target_id, _weight, _unused_rt in rows:
                if target_id not in visited:
                    queue.append((target_id, depth + 1))

    if not graph:
        return []

    # Batch load all visited memories in one query (active only)
    mem_ids = {entry["memory_id"] for entry in graph}
    result = await session.execute(
        select(Memory).where(
            Memory.id.in_(mem_ids),
            Memory.user_id == user.id,
            Memory.is_active.is_(True),
        )
    )
    mem_lookup: dict[int, Memory] = {m.id: m for m in result.scalars().all()}

    for entry in graph:
        mid = entry.pop("memory_id")
        mem = mem_lookup.get(mid)
        if mem:
            entry["memory"] = mem
        # если memory удалена между BFS и batch-load — пропускаем

    return graph


async def get_graph_stats(session: AsyncSession, user_id: int) -> dict:
    """Return graph statistics.

    Covers: node count, edge type breakdown, top hubs, connected components,
    and average degree. Uses SQL aggregation wherever possible; flood-fill
    in Python for connected components.
    """
    # ── 1. Node count (active memories) ──────────────────────────────
    node_count = (
        await session.execute(
            select(func.count())
            .select_from(Memory)
            .where(Memory.user_id == user_id, Memory.is_active)
        )
    ).scalar() or 0

    # ── 2. Edge counts by relation_type ─────────────────────────────
    edge_rows = (
        await session.execute(
            select(
                func.coalesce(MemoryLink.relation_type, "unknown").label("rel_type"),
                func.count().label("cnt"),
            )
            .where(MemoryLink.user_id == user_id)
            .group_by(func.coalesce(MemoryLink.relation_type, "unknown"))
        )
    ).all()
    edges_by_type: dict[str, int] = {r.rel_type: r.cnt for r in edge_rows}
    total_edges = sum(edges_by_type.values())

    # ── 3. Top-5 hub nodes (highest total degree: source + target) ──
    hub_sql = """
        SELECT node_id, SUM(degree) AS total_degree
        FROM (
            SELECT source_id AS node_id, COUNT(*) AS degree
            FROM memory_links
            WHERE user_id = :uid
            GROUP BY source_id
            UNION ALL
            SELECT target_id AS node_id, COUNT(*) AS degree
            FROM memory_links
            WHERE user_id = :uid
            GROUP BY target_id
        ) AS d
        GROUP BY node_id
        ORDER BY total_degree DESC
        LIMIT 5
    """
    hub_rows = (await session.execute(sql_text(hub_sql), {"uid": user_id})).all()

    top_hubs: list[dict] = []
    if hub_rows:
        hub_ids = [int(r[0]) for r in hub_rows]
        mems = (
            (await session.execute(select(Memory).where(Memory.id.in_(hub_ids))))
            .scalars()
            .all()
        )
        mem_map = {m.id: m for m in mems}
        for row in hub_rows:
            nid = int(row[0])
            mem = mem_map.get(nid)
            top_hubs.append(
                {
                    "memory_id": nid,
                    "degree": int(row[1]),
                    "fact": mem.fact[:80] if mem else "?",
                    "contact_id": mem.contact_id if mem else None,
                }
            )

    # ── 4. Connected components (flood fill) ─────────────────────────
    # Load all edges for flood-fill
    all_edges = (
        await session.execute(
            select(MemoryLink.source_id, MemoryLink.target_id)
            .where(MemoryLink.user_id == user_id)
            .distinct()
        )
    ).all()

    active_ids: set[int] = set(
        (
            await session.execute(
                select(Memory.id).where(Memory.user_id == user_id, Memory.is_active)
            )
        )
        .scalars()
        .all()
    )

    # Build undirected adjacency (only edges between active nodes)
    adj: dict[int, set[int]] = defaultdict(set)
    for s, t in all_edges:
        s_id, t_id = int(s), int(t)
        if s_id in active_ids and t_id in active_ids:
            adj[s_id].add(t_id)
            adj[t_id].add(s_id)

    # Nodes that appear in any edge
    nodes_in_edges: set[int] = set()
    for s, t in all_edges:
        s_id, t_id = int(s), int(t)
        nodes_in_edges.add(s_id)
        nodes_in_edges.add(t_id)

    # Isolated = active nodes with no edges
    isolated = len(active_ids - nodes_in_edges)

    # BFS only for connected nodes (non-isolated)
    connected_ids = active_ids & nodes_in_edges
    visited: set[int] = set()
    components = 0
    for nid in connected_ids:
        if nid in visited:
            continue
        components += 1
        queue = deque([nid])
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            for neighbor in adj.get(cur, set()):
                if neighbor not in visited:
                    queue.append(neighbor)

    # ── 5. Average degree ───────────────────────────────────────────
    avg_degree = round(total_edges / max(node_count, 1), 2)

    # ── 6. Return stats ────────────────────────────────────────────
    return {
        "node_count": node_count,
        "total_edges": total_edges,
        "edges_by_type": edges_by_type,
        "top_hubs": top_hubs,
        "components": components,
        "isolated_nodes": isolated,
        "avg_degree": avg_degree,
    }
