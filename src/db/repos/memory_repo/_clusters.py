"""Memory repository — memory clusters."""

from __future__ import annotations

import logging

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Memory, MemoryCluster, MemoryClusterMember

logger = logging.getLogger(__name__)


async def upsert_memory_cluster(
    session: AsyncSession,
    user,
    topic: str,
    *,
    summary: str | None = None,
    fact_count: int | None = None,
) -> MemoryCluster:
    """Создаёт или возвращает существующий кластер по теме."""
    result = await session.execute(
        select(MemoryCluster).where(
            MemoryCluster.user_id == user.id,
            MemoryCluster.topic == topic.lower().strip(),
        )
    )
    cluster = result.scalar_one_or_none()
    if cluster is None:
        cluster = MemoryCluster(user_id=user.id, topic=topic.lower().strip())
        session.add(cluster)
    if summary is not None:
        cluster.summary = summary
    if fact_count is not None:
        cluster.fact_count = fact_count
    await session.flush()
    return cluster


async def list_memory_clusters(session: AsyncSession, user) -> list[MemoryCluster]:
    """Список кластеров памяти."""
    result = await session.execute(
        select(MemoryCluster)
        .where(MemoryCluster.user_id == user.id)
        .order_by(MemoryCluster.fact_count.desc())
    )
    return list(result.scalars().all())


async def add_member(
    session: AsyncSession,
    user_id: int,
    memory_id: int,
    cluster_id: int,
    score: float = 0.5,
) -> None:
    """Добавляет факт в кластер.

    Verifies ownership of both the memory and the cluster before adding
    the member row.
    """
    # Defence-in-depth: verify memory and cluster belong to the user.
    mem = await session.get(Memory, memory_id)
    cluster = await session.get(MemoryCluster, cluster_id)
    if not mem or mem.user_id != user_id:
        logger.warning(
            "add_member: memory %d does not belong to user %d", memory_id, user_id
        )
        return
    if not cluster or cluster.user_id != user_id:
        logger.warning(
            "add_member: cluster %d does not belong to user %d", cluster_id, user_id
        )
        return

    m = MemoryClusterMember(
        user_id=user_id,
        memory_id=memory_id,
        cluster_id=cluster_id,
        relevance_score=score,
    )
    session.add(m)
    await session.flush()


async def get_cluster_members(
    session: AsyncSession,
    user,
    cluster_id: int,
    limit: int = 20,
) -> list[Memory]:
    """Факты кластера, отсортированы по relevance_score."""
    q = (
        select(Memory)
        .join(MemoryClusterMember, Memory.id == MemoryClusterMember.memory_id)
        .where(
            MemoryClusterMember.cluster_id == cluster_id,
            MemoryClusterMember.user_id == user.id,
            Memory.is_active,
        )
        .order_by(MemoryClusterMember.relevance_score.desc())
        .limit(limit)
    )
    r = await session.execute(q)
    return list(r.scalars().all())


async def list_clusters_for_contact(
    session: AsyncSession,
    user,
    contact_id: int | None = None,
) -> list:
    """Кластеры для контакта (или общие)."""
    q = (
        select(
            MemoryCluster,
            func.count(distinct(MemoryClusterMember.memory_id)).label("fact_count"),
        )
        .join(
            MemoryClusterMember,
            MemoryCluster.id == MemoryClusterMember.cluster_id,
        )
        .join(Memory, Memory.id == MemoryClusterMember.memory_id)
        .where(
            MemoryCluster.user_id == user.id,
            Memory.is_active,
        )
    )
    if contact_id is not None:
        q = q.where(Memory.contact_id == contact_id)
    q = (
        q.group_by(MemoryCluster.id)
        .order_by(func.count(distinct(MemoryClusterMember.memory_id)).desc())
        .limit(10)
    )
    r = await session.execute(q)
    return list(r.all())
