"""Memory repository — general memory search."""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Memory, User

logger = logging.getLogger(__name__)


async def list_memories(
    session: AsyncSession,
    user: User,
    *,
    contact_id: int | None = None,
    limit: int | None = None,
    is_active: bool | None = None,
    has_tags: bool | None = None,
) -> list[Memory]:
    query = (
        select(Memory)
        .where(Memory.user_id == user.id)
        .order_by(Memory.created_at.desc())
    )
    if contact_id is not None:
        query = query.where(Memory.contact_id == contact_id)
    if is_active is not None:
        query = query.where(Memory.is_active == is_active)
    if has_tags is not None:
        if has_tags:
            query = query.where(Memory.tags.isnot(None), Memory.tags != "")
        else:
            query = query.where(or_(Memory.tags.is_(None), Memory.tags == ""))
    if limit is not None:
        query = query.limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def search_memories(
    session: AsyncSession,
    user: User,
    query: str,
    *,
    contact_id: int | None = None,
) -> list[Memory]:
    # Lazy import to avoid circular: _search -> _fts
    from src.db.repos.memory_repo._fts import search_memories_fts

    # Пробуем FTS5 сначала; если пусто — ILIKE fallback
    results = await search_memories_fts(session, user, query, contact_id=contact_id)
    if results:
        return results
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == user.id,
            Memory.fact.icontains(query),
        )
        .order_by(Memory.created_at.desc())
    )
    if contact_id is not None:
        stmt = stmt.where(Memory.contact_id == contact_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())
