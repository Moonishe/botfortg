"""Memory Chain Narrative — строит связные рассказы из связанных фактов памяти."""

import logging
from datetime import datetime

from sqlalchemy import or_, select

from src.core.memory.relation_types import RelationType
from src.db.models import Memory, MemoryLink
from src.db.repo import (
    get_contact,
    get_linked_memories,
    get_or_create_user,
    list_memories,
)
from src.db.session import get_session

logger = logging.getLogger(__name__)

RELATION_EMOJI = {
    RelationType.CAUSE: "🎯",
    RelationType.EFFECT: "⚡",
    RelationType.CONTRADICTS: "⚠️",
    RelationType.SUPPORTS: "✅",
    RelationType.CONTINUES: "➡️",
    RelationType.EXAMPLE_OF: "📌",
    RelationType.SUPERSEDES: "🔄",
    None: "•",
}

RELATION_WORD = {
    RelationType.CAUSE: "потому что",
    RelationType.EFFECT: "из-за этого",
    RelationType.CONTRADICTS: "но",
    RelationType.SUPPORTS: "и это подтверждает что",
    RelationType.CONTINUES: "затем",
    RelationType.EXAMPLE_OF: "например",
    RelationType.SUPERSEDES: "обновлено на",
    None: "",
}


async def build_chain(
    session, owner, memory_id: int, max_depth: int = 10
) -> list[dict]:
    """
    Строит цепочку связанных фактов от memory_id в обе стороны.
    Возвращает список фактов в хронологическом порядке.
    Каждый элемент: {memory_id, fact, sentiment, relation_type, related_to, created_at}
    """
    seen = set()
    chain = []
    queue = [memory_id]
    while queue and len(chain) < max_depth:
        mid = queue.pop(0)
        if mid in seen:
            continue
        seen.add(mid)
        linked = await get_linked_memories(session, owner, mid, limit=5)
        for item in linked:
            m = item["memory"]
            if m.id not in seen:
                queue.append(m.id)
                chain.append(
                    {
                        "memory_id": m.id,
                        "fact": m.fact,
                        "sentiment": m.sentiment,
                        "relation_type": item.get("relation_type"),
                        "related_to": mid,
                        "weight": item.get("weight", 0.5),
                        "created_at": m.created_at,
                    }
                )
    return sorted(chain, key=lambda x: x["created_at"] or datetime.min)


async def build_chain_narrative(contact_id: int, owner_id: int) -> str | None:
    """
    Строит связный рассказ обо всех фактах контакта.
    Возвращает HTML-строку или None если не хватает данных.
    """
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        memories = await list_memories(session, owner, contact_id=contact_id)
        if len(memories) < 3:
            return None
        contact = await get_contact(session, owner, contact_id)
        name = contact.display_name if contact else str(contact_id)

        # Строим группы связанных фактов
        seen: set[int] = set()
        chains: list[list[dict]] = []

        # Начинаем с фактов у которых есть связи через MemoryLink (корни цепочек)
        linked_ids: set[int] = set()
        if memories:
            result = await session.execute(
                select(MemoryLink.source_id)
                .where(
                    MemoryLink.source_id.in_([m.id for m in memories]),
                    MemoryLink.user_id == owner.id,
                )
                .distinct()
            )
            linked_ids = {row[0] for row in result.all()}
        roots = [m for m in memories if m.id in linked_ids]
        if not roots:
            roots = memories[:1]

        for root in roots[:3]:  # макс 3 цепочки
            chain = await build_chain(session, owner, root.id, max_depth=8)
            if chain:
                chains.append(chain)
                for c in chain:
                    seen.add(c["memory_id"])

        # Формируем текст
        lines = [f"<b>📖 История отношений с {name}</b>", ""]
        for ci, chain in enumerate(chains):
            if ci > 0:
                lines.append("")
            lines.append(f"<b>Сюжет {ci + 1}:</b>")
            for item in chain:
                emoji = RELATION_EMOJI.get(item["relation_type"], "•")
                word = RELATION_WORD.get(item["relation_type"], "")
                prefix = f"  {emoji} {word} " if word else f"  {emoji} "
                lines.append(f"{prefix}{item['fact']}")

        # Одинокие факты (не в цепочках)
        orphans = [m for m in memories if m.id not in seen]
        if orphans and len(orphans) <= 10:
            lines.append("")
            lines.append("<b>Другие факты:</b>")
            for m in orphans[:5]:
                lines.append(f"  • {m.fact}")

        return "\n".join(lines)


def format_chain_compact(memories: list, contact_name: str = "") -> str:
    """Компактный формат цепочки для вставки в /threads или /chat."""
    if not memories:
        return ""
    name = contact_name or "контакта"
    lines = [f"<b>🔗 История с {name}:</b>"]
    for m in memories[:6]:
        emoji = RELATION_EMOJI.get(
            m.relation_type if hasattr(m, "relation_type") else None,
            "•",
        )
        lines.append(f"{emoji} {m.fact if hasattr(m, 'fact') else str(m)}")
    return "\n".join(lines)


async def follow_supersedes_chain(
    session, owner, start_memory_id: int, max_depth: int = 20
) -> list[dict]:
    """Обход цепочки supersedes-связей в обе стороны от start_memory_id.

    Возвращает список узлов в хронологическом порядке. Каждый элемент:
    {memory_id, fact, is_head, is_tail, created_at, relation_type, related_to}.

    Семантика:
    - link_memories(source=B, target=A, relation_type="supersedes") означает
      «B supersedes A» (B новее, A старее). link_memories создаёт двустороннюю
      связь, поэтому A→B тоже существует с тем же relation_type.
    - is_head=True — самый новый узел в цепочке (head эволюции);
    - is_tail=True — самый старый узел в цепочке (origin).

    Защита от циклов: visited set. На двусторонней связи A↔B
    (link_memories создаёт обе) BFS завершится за один обход, не зациклится.
    """
    if not start_memory_id:
        return []

    visited: set[int] = set()
    queue: list[int] = [start_memory_id]
    nodes: list[dict] = []

    # Загружаем стартовый узел
    start_mem = await session.get(Memory, start_memory_id)
    if start_mem is None or start_mem.user_id != owner.id:
        return []

    while queue and len(visited) < max_depth:
        mid = queue.pop(0)
        if mid in visited:
            continue
        visited.add(mid)

        mem = await session.get(Memory, mid)
        if mem is None or mem.user_id != owner.id:
            continue

        # Найти все supersedes-связи для текущего узла (в обе стороны)
        result = await session.execute(
            select(MemoryLink).where(
                MemoryLink.user_id == owner.id,
                MemoryLink.relation_type == RelationType.SUPERSEDES,
                or_(
                    MemoryLink.source_id == mid,
                    MemoryLink.target_id == mid,
                ),
            )
        )
        for link in result.scalars().all():
            other_id = link.target_id if link.source_id == mid else link.source_id
            if other_id not in visited:
                queue.append(other_id)

    # Загружаем все узлы разом для сортировки
    if visited:
        result = await session.execute(select(Memory).where(Memory.id.in_(visited)))
        mem_map = {m.id: m for m in result.scalars().all()}
    else:
        mem_map = {}

    for mid in visited:
        m = mem_map.get(mid)
        if m is None:
            continue
        nodes.append(
            {
                "memory_id": m.id,
                "fact": m.fact,
                "sentiment": m.sentiment,
                "memory_type": getattr(m, "memory_type", None),
                "created_at": m.created_at,
            }
        )

    # Сортировка по created_at (старые сначала)
    nodes.sort(key=lambda x: x["created_at"] or datetime.min)

    # is_head — самый новый; is_tail — самый старый
    for n in nodes:
        n["is_head"] = False
        n["is_tail"] = False
    if nodes:
        nodes[-1]["is_head"] = True
        nodes[0]["is_tail"] = True
    return nodes
