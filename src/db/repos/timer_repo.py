"""Timer repository — CRUD for persistent timers."""

from __future__ import annotations

import logging

from sqlalchemy import select, delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models._timer import Timer

logger = logging.getLogger(__name__)


async def persist_timer(
    session: AsyncSession,
    timer_id: int,
    fire_at: str,
    label: str,
) -> None:
    """Upsert a timer record."""
    stmt = sqlite_insert(Timer).values(timer_id=timer_id, fire_at=fire_at, label=label)
    stmt = stmt.on_conflict_do_update(
        index_elements=["timer_id"],
        set_={
            "fire_at": stmt.excluded.fire_at,
            "label": stmt.excluded.label,
        },
    )
    await session.execute(stmt)
    await session.flush()


async def delete_timer(session: AsyncSession, timer_id: int) -> bool:
    """Delete a timer by id. Returns True if a row was deleted."""
    result = await session.execute(delete(Timer).where(Timer.timer_id == timer_id))
    deleted = result.rowcount > 0
    if deleted:
        await session.flush()
    return deleted


async def list_pending_timers(
    session: AsyncSession,
) -> list[Timer]:
    """Return all timers whose fire_at is in the future."""
    from datetime import datetime, UTC

    # Compare at second precision: alarms store fire_at without microseconds,
    # so a now-string with microseconds can lexicographically exceed them.
    now_str = datetime.now(UTC).replace(microsecond=0).isoformat()
    result = await session.execute(
        select(Timer).where(Timer.fire_at > now_str).order_by(Timer.fire_at)
    )
    return list(result.scalars().all())


async def delete_expired_timers(session: AsyncSession) -> int:
    """Delete all timers whose fire_at is in the past. Returns count of deleted rows."""
    from datetime import datetime, UTC

    now_str = datetime.now(UTC).replace(microsecond=0).isoformat()
    result = await session.execute(delete(Timer).where(Timer.fire_at <= now_str))
    deleted = result.rowcount
    if deleted:
        await session.flush()
    return deleted
