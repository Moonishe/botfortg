"""Nudge engine — human-in-the-loop confirmation for medium-confidence facts."""

from __future__ import annotations

import logging
from datetime import datetime, UTC
from random import randint
from typing import TYPE_CHECKING

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.compaction.filters import non_task_memory_type_filter
from src.core.compaction.models import NudgeCandidate
from src.core.infra.text_sanitizer import sanitize_html
from src.core.memory.memory_admin import update_memory_text
from src.db.models import Memory
from src.db.repos.memory_repo import save_memory_version

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_NUDGE_INTRO = "🧠 Проверка памяти:\n\n"


def build_nudge_keyboard(candidate: NudgeCandidate) -> InlineKeyboardMarkup:
    """Build confirm/forget/edit inline keyboard for a candidate."""
    memory_id = candidate.memory_id
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Актуально",
                    callback_data=f"memq:nudge:confirm:{memory_id}",
                ),
                InlineKeyboardButton(
                    text="🗑 Забыть",
                    callback_data=f"memq:nudge:forget:{memory_id}",
                ),
                InlineKeyboardButton(
                    text="✏️ Изменить",
                    callback_data=f"memq:nudge:edit:{memory_id}:0",
                ),
            ]
        ]
    )


async def select_nudge_candidates(
    session: AsyncSession, user_id: int, limit: int = 5
) -> list[NudgeCandidate]:
    """Select medium-layer facts that need human confirmation.

    Uses a random offset on an indexed primary-key ordering instead of
    ``ORDER BY RANDOM()`` to avoid full-table sort on large memory tables.
    """
    limit = max(0, limit)
    if limit == 0:
        return []

    where_clause = (
        Memory.user_id == user_id,
        Memory.is_active.is_(True),
        Memory.temporal_layer == "medium",
        Memory.use_count < 3,
        Memory.confidence < 0.7,
        Memory.pinned.is_(False),
        non_task_memory_type_filter(),
    )

    total = (
        await session.execute(
            select(func.count()).select_from(Memory).where(*where_clause)
        )
    ).scalar_one()

    if total == 0:
        return []

    offset = randint(0, max(0, total - limit))

    result = await session.execute(
        select(Memory)
        .where(*where_clause)
        .order_by(Memory.id)
        .offset(offset)
        .limit(limit)
    )
    candidates: list[NudgeCandidate] = []
    for m in result.scalars().all():
        candidates.append(
            NudgeCandidate(
                memory_id=m.id,
                fact=m.fact,
                confidence=m.confidence,
                use_count=m.use_count or 0,
                created_at=m.created_at.isoformat() if m.created_at else None,
            )
        )
    return candidates


async def apply_nudge_decision(
    session: AsyncSession, memory_id: int, action: str, new_fact: str | None = None
) -> bool:
    """Apply user decision: confirm / forget / edit.

    Returns True if the memory was found and updated.
    """
    mem = await session.get(Memory, memory_id)
    if not mem:
        return False
    if not mem.is_active and action != "forget":
        return False

    now = datetime.now(UTC)

    if action == "confirm":
        mem.use_count = (mem.use_count or 0) + 1
        mem.confidence = min(1.0, (mem.confidence or 0.5) + 0.1)
        mem.updated_at = now
        await session.flush()
        return True

    if action == "forget":
        mem.is_active = False
        mem.validity_end = now
        mem.updated_at = now
        await save_memory_version(
            session, memory_id, mem.fact, edited_by="user", reason="nudge_forget"
        )
        await session.flush()
        return True

    if action == "edit":
        if not new_fact:
            return False
        updated = await update_memory_text(
            session, memory_id, new_fact, edit_reason="nudge_edit"
        )
        return updated is not None

    return False


async def nudge_user(
    session: AsyncSession, owner_telegram_id: int, user_id: int
) -> int:
    """Send nudge messages and return number of candidates.

    Uses the notification queue so messages are delivered immediately (they
    carry inline keyboards).
    """
    from src.core.scheduling.notification_queue import notification_queue
    from src.db.models import Notification

    candidates = await select_nudge_candidates(session, user_id)
    if not candidates:
        return 0

    for c in candidates:
        text = f"{_NUDGE_INTRO}«{sanitize_html(c.fact)}»"
        keyboard = build_nudge_keyboard(c)
        await notification_queue.enqueue(
            topic="nudge",
            text=text,
            priority=Notification.PRIORITY_HIGH,
            reply_markup=keyboard,
        )
    return len(candidates)


def parse_memory_id(callback_data: str, index: int = -1) -> int | None:
    """Safely parse memory_id from callback data. Returns None on failure."""
    try:
        return int(callback_data.split(":")[index])
    except (ValueError, IndexError):
        return None
