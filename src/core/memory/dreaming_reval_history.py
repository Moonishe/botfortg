"""History and rollback for Dreaming V3 re-evaluation results.

Extracted from :mod:`src.core.memory.dreaming_reval` to keep the core reval
logic separate from UI / history concerns.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.infra.text_sanitizer import sanitize_html
from src.db.models._memory import Memory, MemoryLink

logger = logging.getLogger(__name__)

__all__ = [
    "recent_reval_history",
    "rollback_reval_history",
]


async def recent_reval_history(owner_telegram_id: int, *, limit: int = 10) -> str:
    """Show recent memories created by Dreaming V3 (``source='dreaming_reval'``).

    Used by ``/memory --reval`` "Подробнее" button.

    Returns formatted HTML string suitable for Telegram messages.
    """
    from src.db.repo import get_or_create_user
    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == owner.id,
                Memory.source == "dreaming_reval",
            )
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        facts = list(result.scalars().all())

    if not facts:
        return "🧠 Нет фактов, созданных через Dreaming V3."

    lines = [f"🧠 <b>Последние {len(facts)} переоценок Dreaming V3:</b>", ""]
    for f in facts:
        ts = f.created_at.strftime("%d.%m %H:%M") if f.created_at else "?"
        status = "✅ активен" if f.is_active else "🚫 деактивирован"
        fact_text = sanitize_html((f.fact or "")[:120])
        lines.append(f"• <code>#{f.id}</code> [{ts}] {status}\n  <i>{fact_text}</i>")
    return "\n".join(lines)


async def rollback_reval_history(owner_telegram_id: int, *, limit: int = 20) -> int:
    """Rollback recent Dreaming V3 changes.

    1. Find ``Memory`` rows with ``source='dreaming_reval'`` and
       ``is_active=True`` (most recent first), up to *limit*.
    2. For each: deactivate it.
    3. Find supersedes ``MemoryLink`` (target → new memory) and reactivate
       the source fact.
    4. Delete the ``MemoryLink`` so the relationship is gone.

    Returns:
        Count of rolled-back revaluations (deactivated new facts).
    """
    from src.db.repo import get_or_create_user
    from src.db.session import get_session

    undone = 0
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)

        new_facts_q = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == owner.id,
                Memory.source == "dreaming_reval",
                Memory.is_active.is_(True),
            )
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        new_facts = list(new_facts_q.scalars().all())
        if not new_facts:
            return 0

        new_ids = [m.id for m in new_facts]

        links_q = await session.execute(
            select(MemoryLink).where(
                MemoryLink.user_id == owner.id,
                MemoryLink.target_id.in_(new_ids),
                MemoryLink.relation_type == "supersedes",
            )
        )
        links = list(links_q.scalars().all())

        new_to_old: dict[int, int] = {int(l.target_id): int(l.source_id) for l in links}

        # M8: атомарный savepoint для каждой пары (деактивация нового +
        # реактивация старого). Если реактивация упадёт — откатываем
        # деактивацию нового, чтобы избежать ситуации «оба неактивны».
        for new_fact in new_facts:
            try:
                # savepoint: атомарная пара деактивация нового + реактивация старого
                async with session.begin_nested() as sp:
                    new_fact.is_active = False
                    new_fact.updated_at = datetime.now(timezone.utc)
                    old_id = new_to_old.get(new_fact.id)
                    if old_id is not None:
                        old = await session.get(Memory, old_id)
                        if old and old.user_id == owner.id:
                            old.is_active = True
                            old.updated_at = datetime.now(timezone.utc)
                    undone += 1
            except Exception:
                logger.exception(
                    "rollback_reval_history: savepoint failed for new_fact=%d, "
                    "skipping to avoid both-inactive state",
                    new_fact.id,
                )

        for link in links:
            await session.delete(link)

        try:
            await session.commit()
        except Exception:
            logger.exception("rollback_reval_history: commit failed")
            await session.rollback()
            return 0

    # Invalidate cache outside the session
    try:
        from src.core.actions.stats_cache import invalidate
        from src.core.memory.memory_recall import bump_recall_version

        await invalidate("mem_")
        await bump_recall_version(owner_telegram_id)
    except Exception:
        pass

    logger.info("rollback_reval_history: undone=%d", undone)
    return undone
