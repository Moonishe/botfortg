"""Memory statistics for core use.

This module lives in the core layer because it uses the stats cache (a core
concern). The query itself stays in the DB layer (SQLAlchemy ORM), so the
core -> db dependency direction is preserved.
"""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.actions.stats_cache import get_cached, set_cache
from src.db.models import Memory, User


async def get_memory_stats(session: AsyncSession, user: User) -> dict:
    """Return cached memory statistics.

    Uses SQL aggregation instead of loading all rows.
    """
    cache_key = f"mem_stats:{user.id}"
    cached = await get_cached(cache_key)
    if cached is not None:
        return cached

    # Scalar aggregates
    r = await session.execute(
        select(
            func.count().label("total"),
            func.coalesce(
                func.sum(case((Memory.confidence >= 0.8, 1), else_=0)), 0
            ).label("high_confidence"),
            func.coalesce(
                func.sum(case((Memory.contact_id.isnot(None), 1), else_=0)), 0
            ).label("with_contact"),
        ).where(Memory.user_id == user.id, Memory.is_active)
    )
    row = r.one()

    # By sentiment
    sent_rows = (
        await session.execute(
            select(
                func.coalesce(Memory.sentiment, "neutral").label("sentiment"),
                func.count().label("cnt"),
            )
            .where(Memory.user_id == user.id, Memory.is_active)
            .group_by(func.coalesce(Memory.sentiment, "neutral"))
        )
    ).all()
    by_sentiment = {sr.sentiment: sr.cnt for sr in sent_rows}

    # By source
    src_rows = (
        await session.execute(
            select(Memory.source, func.count().label("cnt"))
            .where(Memory.user_id == user.id, Memory.is_active)
            .group_by(Memory.source)
        )
    ).all()
    by_source = {sr.source: sr.cnt for sr in src_rows}

    # By memory tier
    tier_rows = (
        await session.execute(
            select(
                Memory.memory_tier.label("tier"),
                func.count().label("cnt"),
            )
            .where(Memory.user_id == user.id, Memory.is_active)
            .group_by(Memory.memory_tier)
        )
    ).all()
    by_tier = {f"tier_{tr.tier}": tr.cnt for tr in tier_rows}

    stats = {
        "total": row.total,
        "by_sentiment": by_sentiment,
        "by_source": by_source,
        "by_tier": by_tier,
        "high_confidence": row.high_confidence,
        "with_contact": row.with_contact,
    }
    await set_cache(cache_key, stats)
    return stats
