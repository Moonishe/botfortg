"""Memory repository — memory candidates."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import MemoryCandidate

logger = logging.getLogger(__name__)


async def add_memory_candidate(
    session: AsyncSession,
    user,
    *,
    fact: str,
    contact_id: int | None = None,
    sentiment: str | None = None,
    memory_type: str | None = None,
    source: str = "chat",
    importance: float = 0.5,
    decay_rate: float = 0.07,
) -> MemoryCandidate:
    # ── Validation ───────────────────────────────────────────────────
    fact = fact.strip()
    if len(fact) < 3 or len(fact) > 10000:
        raise ValueError(f"Fact must be 3–10000 characters, got {len(fact)}")

    candidate = MemoryCandidate(
        user_id=user.id,
        contact_id=contact_id,
        fact=fact,
        sentiment=sentiment,
        memory_type=memory_type,
        source=source,
        importance=importance,
        decay_rate=decay_rate,
    )
    session.add(candidate)
    await session.flush()
    return candidate


async def list_memory_candidates(
    session: AsyncSession,
    user,
    limit: int = 20,
) -> list[MemoryCandidate]:
    result = await session.execute(
        select(MemoryCandidate)
        .where(MemoryCandidate.user_id == user.id)
        .order_by(MemoryCandidate.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def delete_memory_candidate(
    session: AsyncSession,
    user,
    candidate_id: int,
) -> bool:
    obj = await session.get(MemoryCandidate, candidate_id)
    if obj and obj.user_id == user.id:
        await session.delete(obj)
        return True
    return False
