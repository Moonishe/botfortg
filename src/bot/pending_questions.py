"""Pending question queue — accumulate questions during async operations.

This module is a thin re-export shim. The canonical implementation lives
in :mod:`src.core.memory.pending_questions`; see that module for the
in-memory + DB queue logic. Bot-side callers can keep importing from
``src.bot.pending_questions`` for backward compatibility.
"""

from __future__ import annotations

import asyncio
import logging

from src.db.session import get_session

logger = logging.getLogger(__name__)

_pending: dict[int, list[str]] = {}  # telegram_id → questions
_lock = asyncio.Lock()


async def get_pending(telegram_id: int) -> list[str]:
    """Atomically load and drain pending questions from DB + in-memory queue.

    _lock serialises access per-user to prevent duplicate delivery
    when two concurrent calls race on DB load+delete.
    """
    async with _lock:
        questions: list[str] = []
        # DB first — load any that survived restart (safe: delete happens after)
        try:
            async with get_session() as session:
                from src.db.repo import get_pending_questions, get_or_create_user

                owner = await get_or_create_user(session, telegram_id)
                db_questions = await get_pending_questions(session, owner.id)
                questions.extend(db_questions)
                # Delete loaded questions from DB so they are not re-loaded
                # on the next call (idempotent — only removes what was loaded).
                if db_questions:
                    from src.core.memory.pending_questions import (
                        delete_pending_questions,
                    )

                    await delete_pending_questions(telegram_id)
        except Exception:
            logger.debug("Failed to load pending questions from DB", exc_info=True)
        # In-memory (pop after DB so questions are not lost on DB failure)
        questions.extend(_pending.pop(telegram_id, []))
        return questions


async def has_pending(telegram_id: int) -> bool:
    """Проверяет наличие ожидающих вопросов в памяти и в БД."""
    async with _lock:
        if _pending.get(telegram_id):
            return True
    # Проверяем также БД (могли остаться после рестарта)
    try:
        async with get_session() as session:
            from src.db.repo import get_or_create_user

            owner = await get_or_create_user(session, telegram_id)
            from sqlalchemy import select, func

            from src.db.models import PendingQuestion

            r = await session.execute(
                select(func.count())
                .select_from(PendingQuestion)
                .where(PendingQuestion.owner_id == owner.id)
            )
            return r.scalar_one() > 0
    except Exception:
        logger.debug("Failed to check pending questions in DB", exc_info=True)
        return False
