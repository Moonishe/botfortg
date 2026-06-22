"""Memory Inbox callback handlers — extracted from memory_cmd.py (Stage 4 refactor).

Handlers:
  - cb_memory_inbox (memb:confirm|discard|temporary|permanent|edit) — handles
    approval/rejection of MemoryCandidate rows from the Inbox.
  - cb_mem_to_task (mem:totask:<memory_id>) — creates a Commitment (task) from
    an existing memory fact.

These handlers live in their own module to keep memory_cmd.py focused on
command surface (/memory, /remember, /forget, /habits, etc.) and to make the
prompt-injection boundary for inbox intake easy to audit.
"""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.bot.filters import OwnerOnly
from src.core.infra.text_sanitizer import sanitize_html
from src.core.security.prompt_injection_scanner import scan_content
from src.db.models import Memory, MemoryCandidate
from src.db.repo import (
    add_commitment,
    get_commitment_by_source_memory,
    get_or_create_user
)
from src.core.memory.memory_service import save_memory_single
from src.db.session import get_session


logger = logging.getLogger(__name__)
router = Router(name="memory_inbox")
router.callback_query.filter(OwnerOnly())


# ── Memory Inbox (memb:*) handlers ──────────────────────────────────


@router.callback_query(F.data.startswith("memb:"))
async def cb_memory_inbox(callback: CallbackQuery) -> None:
    """Обрабатывает кнопки Inbox для MemoryCandidate."""
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    parts = callback.data.split(":")
    action = parts[1]
    candidate_id = int(parts[2])

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        candidate = await session.get(MemoryCandidate, candidate_id)

        if candidate is None or candidate.user_id != owner.id:
            await callback.answer("Факт не найден", show_alert=True)
            return

        # Prompt-injection scan (защита для confirm / temporary / permanent)
        try:
            scan_result = scan_content(candidate.fact, "memory_intake")
            if scan_result.blocked:
                await callback.answer(
                    "⛔ Контент не прошёл проверку безопасности.", show_alert=True
                )
                return
        except Exception:
            logger.warning(
                "scan_content failed, passing through: %.50s", candidate.fact
            )

        if action == "confirm":
            # Перенести в Memory как есть
            await save_memory_single(
                session,
                owner,
                fact=candidate.fact,
                contact_id=candidate.contact_id,
                sentiment=candidate.sentiment or None,
                source=candidate.source,
                importance=candidate.importance,
                decay_rate=candidate.decay_rate,
                confidence=0.5,
                memory_type=None)
            await session.delete(candidate)
            await callback.message.edit_text(
                f"✅ Запомнил: <i>{sanitize_html(candidate.fact)}</i>"
            )
            await callback.answer("Факт сохранён")

        elif action == "discard":
            await session.delete(candidate)
            await callback.message.edit_text(
                f"🗑 Удалил: <i>{sanitize_html(candidate.fact)}</i>"
            )
            await callback.answer("Факт удалён")

        elif action == "temporary":
            # Перенести с memory_type="temporary", decay_rate=0.3 (быстро протухнет)
            await save_memory_single(
                session,
                owner,
                fact=candidate.fact,
                contact_id=candidate.contact_id,
                sentiment=candidate.sentiment or None,
                source=candidate.source,
                memory_type="temporary",
                importance=candidate.importance,
                decay_rate=0.3,
                confidence=0.5)
            await session.delete(candidate)
            await callback.message.edit_text(
                f"⏳ Сохранено на неделю: <i>{sanitize_html(candidate.fact)}</i>"
            )
            await callback.answer("Факт сохранён временно")

        elif action == "permanent":
            # Перенести с decay_rate=0.01 (почти не протухнет)
            await save_memory_single(
                session,
                owner,
                fact=candidate.fact,
                contact_id=candidate.contact_id,
                sentiment=candidate.sentiment or None,
                source=candidate.source,
                importance=min(1.0, candidate.importance + 0.2),
                decay_rate=0.01,
                confidence=0.5,
                memory_type=None)
            await session.delete(candidate)
            await callback.message.edit_text(
                f"♾ Сохранено навсегда: <i>{sanitize_html(candidate.fact)}</i>"
            )
            await callback.answer("Факт сохранён навсегда")

        elif action == "edit":
            await session.delete(candidate)
            await callback.message.edit_text(
                f"✏️ Напиши исправленный текст для факта:\n\n"
                f"<i>{sanitize_html(candidate.fact)}</i>\n\n"
                f"<code>/remember исправленный текст</code>"
            )
            await callback.answer("Напиши /remember с исправленным текстом")

        else:
            await callback.answer("Неизвестное действие")


@router.callback_query(F.data.startswith("mem:totask:"))
async def cb_mem_to_task(callback: CallbackQuery) -> None:
    """Создать задачу (Commitment) из факта памяти."""
    memory_id = int(callback.data.split(":")[2])
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        mem = await session.get(Memory, memory_id)
        if mem is None or mem.user_id != owner.id:
            await callback.answer("Факт не найден", show_alert=True)
            return

        # Проверяем, нет ли уже задачи для этого факта
        existing = await get_commitment_by_source_memory(session, owner.id, mem.id)
        if existing:
            await callback.answer("Задача уже существует", show_alert=True)
            return

        # Создаём обязательство со ссылкой на факт памяти
        await add_commitment(
            session,
            user_id=owner.id,
            peer_id=mem.contact_id or 0,
            peer_name=None,
            message_id=None,
            direction="mine",
            text=mem.fact,
            deadline_at=None,
            source_memory_id=mem.id,
        )

    if callback.message:
        await callback.message.edit_text(
            sanitize_html(f"📋 Задача создана:\n<i>{mem.fact}</i>")
        )
    await callback.answer("✅ Задача создана")
