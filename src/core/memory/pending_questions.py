"""Отслеживание вопросов, на которые модель не смогла ответить.

Canonical home for the pending-question queue. The previous bot-side
implementation has been folded into :func:`add_question` here so that
``src/core/*`` can call it without violating the layering rule.
"""

import asyncio
import logging
import time
from typing import Any

from src.db.session import get_session

logger = logging.getLogger(__name__)

# In-memory хранилище: {telegram_id: [{"question": ..., "context": ..., "ts": ...}]}
_pending: dict[int, list[dict[str, Any]]] = {}
_pending_lock = asyncio.Lock()

_PENDING_TTL = 7 * 86400  # 7 дней
_save_counter: int = 0
_CLEANUP_EVERY_N = 100  # cleanup раз в ~100 вызовов


def _cleanup_stale_pending() -> None:
    """Удаляет pending-записи старше _PENDING_TTL."""
    now = time.time()
    cutoff = now - _PENDING_TTL
    for uid in list(_pending):
        _pending[uid] = [q for q in _pending[uid] if q.get("ts", 0) > cutoff]
        if not _pending[uid]:
            del _pending[uid]


def _append_in_memory(telegram_id: int, question: str, context: str = "") -> None:
    """Append a question to the in-memory queue under the lock.

    Caps per-user list at 20 items and bumps the cleanup counter.
    Must be called while holding _pending_lock.
    """
    global _save_counter
    _pending.setdefault(telegram_id, []).append(
        {
            "question": (question or "")[:500],
            "context": context[:200],
            "ts": time.time(),
        }
    )
    # Cap at 20 items per user
    if len(_pending[telegram_id]) > 20:
        _pending[telegram_id] = _pending[telegram_id][-20:]
    _save_counter += 1
    if _save_counter % _CLEANUP_EVERY_N == 0:
        _cleanup_stale_pending()


async def save_pending(telegram_id: int, question: str, context: str = "") -> None:
    """Сохраняет вопрос, на который не нашлось ответа (in-memory only)."""
    async with _pending_lock:
        _append_in_memory(telegram_id, question, context)


async def get_pending(telegram_id: int) -> list[dict[str, Any]]:
    """Возвращает список неотвеченных вопросов И удаляет их из in-memory очереди.

    Вызывающая сторона должна показать вопросы пользователю.
    После показа — вопросы считаются обработанными.
    """
    async with _pending_lock:
        return _pending.pop(telegram_id, [])


async def peek_pending(telegram_id: int) -> list[dict[str, Any]]:
    """Возвращает список неотвеченных вопросов, не удаляя их из очереди.

    Используется для чтения в промпт/снапшот без пометки обработанными.
    """
    async with _pending_lock:
        return list(_pending.get(telegram_id, []))


async def delete_pending_questions(telegram_id: int) -> None:
    """Полностью удаляет pending-вопросы: in-memory + DB.

    Вызывать после того как вопросы показаны пользователю.
    """
    # In-memory
    async with _pending_lock:
        _pending.pop(telegram_id, None)
    # DB
    try:
        async with get_session() as session:
            from src.db.repo import get_or_create_user
            from sqlalchemy import delete as sa_delete

            from src.db.models import PendingQuestion

            owner = await get_or_create_user(session, telegram_id)
            await session.execute(
                sa_delete(PendingQuestion).where(PendingQuestion.owner_id == owner.id)
            )
            await session.commit()
    except Exception:
        logger.debug(
            "Failed to delete pending questions from DB for user %d",
            telegram_id,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# add_question — DB + in-memory queue, canonical entry point
# ---------------------------------------------------------------------------
# The bot-side implementation previously living in
# ``src.bot.pending_questions`` has been moved here so that ``src/core/*``
# can call it without reaching upward into the bot layer. Behaviour is preserved:
# in-memory fast path + DB persistence.
# ---------------------------------------------------------------------------


async def add_question(telegram_id: int, question: str) -> None:
    """Persist a follow-up question for the given owner.

    Stores both in :data:`_pending` (fast in-memory queue) and in the
    ``pending_questions`` table so that the question survives restarts.
    """
    # In-memory (fast)
    async with _pending_lock:
        _append_in_memory(telegram_id, question)
    # DB (persistent)
    try:
        async with get_session() as session:
            from src.db.repo import add_pending_question, get_or_create_user

            owner = await get_or_create_user(session, telegram_id)
            await add_pending_question(session, owner.id, question)
    except Exception:
        logger.debug("Failed to persist pending question", exc_info=True)


# ── Snapshot support (Issue 2: public API for SnapshotEngine) ──────


async def capture_state():
    """Public snapshot of _pending (JSON-serializable)."""
    async with _pending_lock:
        return {str(tg): qs for tg, qs in _pending.items()}


async def restore_state(data):
    """Restore _pending from a snapshot dict."""
    if not data:
        return
    async with _pending_lock:
        for tg_str, qs in data.items():
            try:
                _pending[int(tg_str)] = qs
            except Exception:
                logger.exception("Failed to restore pending questions for %s", tg_str)
