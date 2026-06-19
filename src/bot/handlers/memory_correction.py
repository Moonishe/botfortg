"""FSM-based handler for /memory --correct fact correction.

Migrated from FSM-lite pattern (private _PENDING_CORRECTIONS dict with custom
filter) to native aiogram FSM. The writer site (in `memory_cmd.cmd_memory`)
sets `MemoryCorrectionStates.waiting_new_text` and stores the pending data
in FSM storage. This module owns the consumer handler.

TTL semantics: aiogram FSM has no built-in TTL, so we store `set_at_ts` in
state data and check it lazily on the next message. Additionally, a background
asyncio task (`schedule_correction_ttl_cleanup`) is spawned after setting the
state to clear it after CORRECTION_TTL_SECONDS if no message arrives.

Cancel paths: the global `/cancel` handler in `login.cmd_cancel` clears any
FSM state, so no dedicated cancel command is needed here. The
`cb_memreval` callbacks (cancel/reject/permanent) in `memory_cmd` also call
`state.clear()` so the user can act on the inline keyboard without typing.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.bot.states import MemoryCorrectionStates
from src.core.infra.task_manager import track_ff
from src.db.models import Memory
from src.db.repo import get_or_create_user
from src.db.session import get_session

logger = logging.getLogger(__name__)

router = Router(name="memory_correction")
router.message.filter(OwnerOnly())

# TTL for pending correction (300 seconds, matches legacy behavior).
CORRECTION_TTL_SECONDS = 300

# Module-level task storage for pending correction TTL cleanups.
# Storing asyncio.Task in FSM data breaks JSON-based storages (e.g. RedisStorage),
# so we keep the task object at the module level keyed by (user_id, chat_id).
_ttl_tasks: dict[tuple[int, int], asyncio.Task] = {}


def _ttl_key(message: Message) -> tuple[int, int]:
    """Return the canonical key for the TTL task dict."""
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id if message.chat else 0
    return (user_id, chat_id)


def cancel_correction_ttl_cleanup(user_id: int, chat_id: int) -> None:
    """Cancel any pending TTL cleanup task for the given user/chat.

    Called from external cancel paths (``cb_memreval``, ``cmd_cancel``)
    that don't have access to the Message object needed for ``_ttl_key``.
    """
    task = _ttl_tasks.pop((user_id, chat_id), None)
    if task is not None and not task.done():
        task.cancel()


async def clear_correction_state_if_pending(
    state: FSMContext, user_id: int, chat_id: int
) -> bool:
    """Cancel the TTL task and clear the FSM state if a correction is pending.

    Returns ``True`` if the state was cleared, ``False`` otherwise.
    """
    current = await state.get_state()
    if current != MemoryCorrectionStates.waiting_new_text.state:
        return False
    cancel_correction_ttl_cleanup(user_id, chat_id)
    await state.clear()
    return True


async def schedule_correction_ttl_cleanup(state: FSMContext, message: Message) -> None:
    """Schedule a background task that clears the FSM state after TTL.

    Called by ``memory_cmd`` after setting ``waiting_new_text`` state.
    The task is tracked via ``track_ff`` for graceful shutdown.

    The task is stored in a module-level dict keyed by ``(user_id, chat_id)``
    so that ``handle_pending_correction`` can cancel it if the user submits a
    correction before the TTL fires. This avoids putting non-JSON-serialisable
    asyncio.Task objects into FSM storage (required for RedisStorage).
    """
    key = _ttl_key(message)

    # Cancel any previous TTL cleanup task for this key to avoid orphaned
    # tasks when the user invokes /memory --correct multiple times.
    # Pop old task so its finally block (which checks identity via
    # current_task()) won't remove the replacement task we store below.
    old_task = _ttl_tasks.pop(key, None)
    if old_task is not None and not old_task.done():
        old_task.cancel()

    async def _cleanup() -> None:
        my_task = asyncio.current_task()
        try:
            await asyncio.sleep(CORRECTION_TTL_SECONDS)
            current = await state.get_state()
            if current == MemoryCorrectionStates.waiting_new_text.state:
                await state.clear()
        except asyncio.CancelledError:
            # Task was cancelled — either by a new schedule_correction_ttl_cleanup
            # call, by handle_pending_correction receiving a message, or by
            # cb_memreval/cmd_cancel clearing the state.  Do nothing.
            pass
        finally:
            # Only clean up if we are still the task associated with this key —
            # otherwise we'd remove a replacement task created by a later call.
            if _ttl_tasks.get(key) is my_task:
                _ttl_tasks.pop(key, None)

    task = asyncio.create_task(_cleanup())
    track_ff(task)
    _ttl_tasks[key] = task


@router.message(MemoryCorrectionStates.waiting_new_text)
async def handle_pending_correction(message: Message, state: FSMContext) -> None:
    """Обрабатывает текст, если у пользователя есть pending /memory --correct."""
    if message.from_user is None:
        return  # channel posts / anonymous — no user context
    user_id = message.from_user.id

    # Cancel the background TTL cleanup task (if still running) —
    # user has submitted a correction before the TTL fired.
    task = _ttl_tasks.pop(_ttl_key(message), None)
    if task is not None and not task.done():
        task.cancel()

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
        await update_memory_text(session, owner, memory_id, new_text)
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Факт #{memory_id} обновлён:\n\n"
        f"<s>{sanitize_html(old_fact)}</s>\n"
        f"→ <i>{sanitize_html(new_text)}</i>"
    )
