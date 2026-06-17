"""Telegram callback handlers for nudge (human-in-the-loop memory checks)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.bot.filters import OwnerOnly
from src.core.compaction.nudge import apply_nudge_decision, parse_memory_id
from src.db.session import get_session

nudge_router = Router(name="compaction_nudge")
nudge_router.callback_query.filter(OwnerOnly())


@nudge_router.callback_query(F.data.startswith("memq:nudge:confirm:"))
async def _cb_nudge_confirm(callback: CallbackQuery) -> None:
    """User confirmed the fact is still relevant."""
    if not callback.data:
        await callback.answer("Ошибка: пустые данные.")
        return
    memory_id = parse_memory_id(callback.data)
    if memory_id is None:
        await callback.answer("Ошибка: неверный формат данных.")
        return

    async with get_session() as session:
        ok = await apply_nudge_decision(session, memory_id, "confirm")
        await session.commit()

    if ok and callback.message:
        await callback.message.edit_text("✅ Факт подтверждён.")
    await callback.answer("Актуально")


@nudge_router.callback_query(F.data.startswith("memq:nudge:forget:"))
async def _cb_nudge_forget(callback: CallbackQuery) -> None:
    """User wants the fact forgotten."""
    if not callback.data:
        await callback.answer("Ошибка: пустые данные.")
        return
    memory_id = parse_memory_id(callback.data)
    if memory_id is None:
        await callback.answer("Ошибка: неверный формат данных.")
        return

    async with get_session() as session:
        ok = await apply_nudge_decision(session, memory_id, "forget")
        await session.commit()

    if ok and callback.message:
        await callback.message.edit_text("🗑 Факт забыт.")
    await callback.answer("Забыт")


@nudge_router.callback_query(F.data.startswith("memq:nudge:edit:"))
async def _cb_nudge_edit(callback: CallbackQuery) -> None:
    """User wants to edit the fact — ask for a new message."""
    if not callback.data:
        await callback.answer("Ошибка: пустые данные.")
        return
    memory_id = parse_memory_id(callback.data, index=-2)
    if memory_id is None:
        await callback.answer("Ошибка: неверный формат данных.")
        return
    if callback.message:
        await callback.message.edit_text(
            "✏️ Ответь новой версией этого факта одним сообщением."
        )
    await callback.answer("Изменить")
