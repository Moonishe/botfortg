"""Memory repository — impact analysis."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Commitment,
    Contact,
    Memory,
    MemoryCluster,
    MemoryClusterMember,
    MemoryLink,
)

logger = logging.getLogger(__name__)


@dataclass
class ContactImpact:
    """Результат impact analysis для контакта."""

    contact_id: int
    contact_name: str
    direct_facts: list[Memory]
    related_contacts: list[dict]  # [{"id": int, "name": str, "via_fact": str}]
    topics: list[str]
    upcoming_events: list[dict]  # [{"text": str, "deadline": str}]
    total_nodes: int


async def contact_impact(
    session: AsyncSession,
    user_id: int,
    contact_id: int,
    max_depth: int = 2,
) -> ContactImpact:
    """Полный граф зависимостей контакта.

    Возвращает:
    - прямые факты о контакте
    - связанные контакты через MemoryLink
    - темы из кластеров
    - активные напоминания/дедлайны
    """
    # 1. Direct facts
    facts: list[Memory] = list(
        (
            await session.execute(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.contact_id == contact_id,
                    Memory.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )

    fact_ids = [f.id for f in facts]

    # 2. Related contacts via MemoryLink
    related_contacts: list[dict] = []
    if fact_ids:
        links_result = await session.execute(
            select(MemoryLink).where(
                MemoryLink.user_id == user_id,
                or_(
                    MemoryLink.source_id.in_(fact_ids),
                    MemoryLink.target_id.in_(fact_ids),
                ),
            )
        )
        links = links_result.scalars().all()

        neighbor_ids: set[int] = set()
        for link in links:
            other_id = link.target_id if link.source_id in fact_ids else link.source_id
            if other_id not in fact_ids:
                neighbor_ids.add(other_id)

        if neighbor_ids:
            neighbor_mems_result = await session.execute(
                select(Memory).where(
                    Memory.id.in_(list(neighbor_ids)),
                    Memory.is_active.is_(True),
                    Memory.contact_id.isnot(None),
                    Memory.contact_id != contact_id,
                )
            )
            neighbor_mems = neighbor_mems_result.scalars().all()

            # Batch-load contact names to avoid N+1
            neighbor_cids: list[int] = []
            seen_contacts: set[int] = set()
            for nm in neighbor_mems:
                if nm.contact_id and nm.contact_id not in seen_contacts:
                    seen_contacts.add(nm.contact_id)
                    neighbor_cids.append(nm.contact_id)

            name_map: dict[int, str] = {}
            if neighbor_cids:
                names_result = await session.execute(
                    select(Contact.peer_id, Contact.display_name).where(
                        Contact.user_id == user_id,
                        Contact.peer_id.in_(neighbor_cids),
                    )
                )
                name_map = {
                    int(r[0]): r[1] or f"contact#{r[0]}" for r in names_result.all()
                }

            for nm in neighbor_mems:
                if nm.contact_id and nm.contact_id in seen_contacts:
                    cname = name_map.get(nm.contact_id, f"contact#{nm.contact_id}")
                    # Only add once per contact
                    if any(rc["id"] == nm.contact_id for rc in related_contacts):
                        continue
                    related_contacts.append(
                        {
                            "id": nm.contact_id,
                            "name": cname,
                            "via_fact": (nm.fact or "")[:60],
                        }
                    )

    # 3. Topics from clusters
    topics: list[str] = []
    if fact_ids:
        cluster_rows = (
            (
                await session.execute(
                    select(MemoryCluster.topic)
                    .join(
                        MemoryClusterMember,
                        MemoryClusterMember.cluster_id == MemoryCluster.id,
                    )
                    .where(
                        MemoryCluster.user_id == user_id,
                        MemoryClusterMember.memory_id.in_(fact_ids),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        topics = [t for t in cluster_rows if t]

    # 4. Upcoming commitments
    contact_name_row = await session.execute(
        select(Contact.display_name).where(
            Contact.user_id == user_id, Contact.peer_id == contact_id
        )
    )
    contact_name = contact_name_row.scalar() or f"contact#{contact_id}"

    events: list[dict] = []
    commitments_result = await session.execute(
        select(Commitment).where(
            Commitment.user_id == user_id,
            Commitment.status == "open",
        )
    )
    commitments = commitments_result.scalars().all()

    for c in commitments:
        if c.peer_name and contact_name and contact_name.lower() in c.peer_name.lower():
            events.append(
                {
                    "text": c.text or "",
                    "deadline": c.deadline_at.isoformat() if c.deadline_at else "",
                }
            )

    return ContactImpact(
        contact_id=contact_id,
        contact_name=contact_name,
        direct_facts=facts,
        related_contacts=related_contacts[:10],
        topics=topics[:10],
        upcoming_events=events[:5],
        total_nodes=len(facts) + len(related_contacts),
    )
