"""Callback handlers for draft suggestion inline keyboard (send/edit/ignore)."""

from __future__ import annotations

import hashlib
import logging
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.bot.filters import OwnerOnly
from src.bot.handlers.smart_keyboard import smart_post_action_keyboard
from src.bot.states import DraftStates
from src.core.contacts.send_guard import store_undo
from src.userbot import get_active_telethon_client


logger = logging.getLogger(__name__)

router = Router(name="draft_actions")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


# in-memory store: draft_hash -> (timestamp, full draft text)
_draft_texts: dict[str, tuple[float, str]] = {}
DRAFT_TTL_SECONDS = 30 * 60  # 30 минут


def _draft_cleanup() -> None:
    """Удаляет черновики старше DRAFT_TTL_SECONDS."""
    now = time.time()
    stale = [k for k, (ts, _) in _draft_texts.items() if now - ts > DRAFT_TTL_SECONDS]
    for k in stale:
        del _draft_texts[k]


def store_draft(draft_text: str) -> str:
    """Сохраняет черновик и возвращает hash-ключ для callback'ов."""
    draft_hash = hashlib.sha256(draft_text.encode()).hexdigest()[:8]
    _draft_texts[draft_hash] = (time.time(), draft_text)
    _draft_cleanup()
    return draft_hash


def draft_keyboard(peer_id: int, draft_hash: str) -> InlineKeyboardMarkup:
    """Строит inline-клавиатуру для черновика."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Отправить",
                    callback_data=f"draft:send:{peer_id}:{draft_hash}",
                ),
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"draft:edit:{peer_id}:{draft_hash}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Игнорировать",
                    callback_data=f"draft:ignore:{peer_id}:{draft_hash}",
                ),
            ],
        ]
    )


# ── Отправка ──


@router.callback_query(F.data.startswith("draft:send:"))
async def cb_draft_send(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    peer_id = int(parts[2])
    draft_hash = parts[3]

    draft_data = _draft_texts.pop(draft_hash, None)
    if draft_data is None:
        await callback.answer("Черновик устарел или не найден", show_alert=True)
        return
    ts, draft_text = draft_data
    if time.time() - ts > DRAFT_TTL_SECONDS:
        await callback.answer("Черновик устарел", show_alert=True)
        return

    client = get_active_telethon_client(callback.from_user.id)
    if client is None:
        await callback.answer("Нет активной сессии. Сначала /login.", show_alert=True)
        return

    try:
        entity = await client.get_entity(peer_id)
        sent_msg = await client.send_message(entity=entity, message=draft_text)
        if callback.message:
            await store_undo(callback.from_user.id, peer_id, sent_msg.id, draft_text)
            after_kb = smart_post_action_keyboard("send", {"peer_id": str(peer_id)})
            await callback.message.edit_text("✅ Отправлено! 🚀", reply_markup=after_kb)
    except ValueError as e:
        if callback.message:
            await callback.message.edit_text(f"❌ Ошибка 😞: {e}")
        else:
            await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    except Exception as e:
        from telethon.errors import FloodWaitError

        if isinstance(e, FloodWaitError):
            if callback.message:
                await callback.message.edit_text(f"❌ Flood wait ⏳: {e.seconds}с")
            else:
                await callback.answer(f"❌ Flood wait: {e.seconds}с", show_alert=True)
        else:
            await callback.answer(f"❌ Ошибка отправки: {e}", show_alert=True)
    await callback.answer()


# ── Игнорировать ──


@router.callback_query(F.data.startswith("draft:ignore:"))
async def cb_draft_ignore(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    draft_hash = parts[3]
    _draft_texts.pop(draft_hash, None)
    if callback.message:
        await callback.message.edit_text("🗑 Пропущено")
    await callback.answer()


# ── Редактировать ──


@router.callback_query(F.data.startswith("draft:edit:"))
async def cb_draft_edit(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    peer_id = int(parts[2])
    draft_hash = parts[3]

    await state.set_state(DraftStates.waiting_edit)
    await state.set_data({"peer_id": peer_id, "draft_hash": draft_hash})
    await callback.message.answer(
        "Пришли новый текст черновика для отправки. /cancel — отмена."
    )
    await callback.answer()


@router.message(DraftStates.waiting_edit)
async def step_draft_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    peer_id = data.get("peer_id")
    new_text = (message.text or "").strip()
    if not new_text:
        await message.answer("Пустой текст. Повтори или /cancel.")
        return

    client = get_active_telethon_client(message.from_user.id)
    if client is None:
        await message.answer("Нет активной сессии. Сначала /login.")
        await state.clear()
        return

    try:
        entity = await client.get_entity(peer_id)
        sent_msg = await client.send_message(entity=entity, message=new_text)
        await store_undo(message.from_user.id, peer_id, sent_msg.id, new_text)
        after_kb = smart_post_action_keyboard("edit", {"peer_id": str(peer_id)})
        await message.answer("✅ Отправлено! 🚀", reply_markup=after_kb)
    except Exception as e:
        await state.clear()
        await message.answer(f"❌ Ошибка отправки 😞: {e}")
    await state.clear()
