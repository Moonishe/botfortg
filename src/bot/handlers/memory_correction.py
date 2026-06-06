"""FSM-based handler for /memory --correct fact correction.

Migrated from FSM-lite pattern (private _PENDING_CORRECTIONS dict with custom
filter) to native aiogram FSM. The writer site (in `memory_cmd.cmd_memory`)
sets `MemoryCorrectionStates.waiting_new_text` and stores the pending data
in FSM storage. This module owns the consumer handler.

TTL semantics: aiogram FSM has no built-in TTL, so we store `set_at_ts` in
state data and check it lazily on the next message — the same opportunistic
cleanup the legacy filter used to perform.

Cancel paths: the global `/cancel` handler in `login.cmd_cancel` clears any
FSM state, so no dedicated cancel command is needed here. The
`cb_memreval` callbacks (cancel/reject/permanent) in `memory_cmd` also call
`state.clear()` so the user can act on the inline keyboard without typing.
"""

from __future__ import annotations

import logging
import time

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.bot.states import MemoryCorrectionStates
from src.db.models import Memory
from src.db.repo import get_or_create_user
from src.db.session import get_session

logger = logging.getLogger(__name__)

router = Router(name="memory_correction")
router.message.filter(OwnerOnly())

# TTL for pending correction (300 seconds, matches legacy behavior).
CORRECTION_TTL_SECONDS = 300


@router.message(MemoryCorrectionStates.waiting_new_text)
async def handle_pending_correction(message: Message, state: FSMContext) -> None:
    """Обрабатывает текст, если у пользователя есть pending /memory --correct."""
    user_id = message.from_user.id

    # Lazy TTL check — FSM has no built-in TTL, so we check set_at_ts
    # stored by the writer (cmd_memory --correct) on each message.
    data = await state.get_data()
    set_at_ts = data.get("set_at_ts", 0)
    if time.monotonic() - set_at_ts > CORRECTION_TTL_SECONDS:
        await state.clear()
        await message.answer(
            "⏰ Время на исправление вышло (5 минут). "
            "Начни заново: /memory --correct <id>."
        )
        return

    new_text = (message.text or "").strip()
    if not new_text or len(new_text) < 3:
        await message.answer("Текст слишком короткий. Напиши заново или /cancel.")
        return
    if len(new_text) > 500:
        await message.answer(
            f"Слишком длинный текст ({len(new_text)} > 500). Сократи и пришли заново."
        )
        return

    memory_id = data.get("memory_id")
    if memory_id is None:
        # Defensive: state was set but data is incomplete — clear and bail.
        await state.clear()
        await message.answer(
            "❌ Состояние исправления потеряно. Начни заново: /memory --correct <id>."
        )
        return

    # Scan user-supplied correction text for prompt injection (lazy import).
    from src.core.security.prompt_injection_scanner import scan_content

    scan_result = scan_content(new_text, "memory_correction")
    if scan_result.blocked:
        await state.clear()
        await message.answer("⛔ Контент не прошёл проверку безопасности.")
        return

    # Lazy import — heavy module, only needed when correction succeeds.
    from src.core.infra.text_sanitizer import sanitize_html
    from src.core.memory.memory_admin import update_memory_text

    async with get_session() as session:
        owner = await get_or_create_user(session, user_id)
        mem = await session.get(Memory, memory_id)
        if not mem or mem.user_id != owner.id:
            await state.clear()
            await message.answer("❌ Факт не найден, отменяю.")
            return
        old_fact = mem.fact
        await update_memory_text(session, memory_id, new_text)
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Факт #{memory_id} обновлён:\n\n"
        f"<s>{sanitize_html(old_fact)}</s>\n"
        f"→ <i>{sanitize_html(new_text)}</i>"
    )
