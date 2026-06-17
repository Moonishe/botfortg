"""Callback handlers for draft suggestion inline keyboard (send/edit/ignore/variants)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from telethon.errors import FloodWaitError

if TYPE_CHECKING:
    from aiogram.fsm.context import FSMContext

from src.agents.draft_agent import draft_variants
from src.bot.filters import OwnerOnly
from src.bot.handlers.smart_keyboard import smart_post_action_keyboard
from src.bot.states import DraftStates
from src.core.infra.key_guard import safe_str
from src.core.infra.text_sanitizer import sanitize_html
from src.db.repo import get_or_create_user as _get_or_create_user
from src.db.session import get_session
from src.llm.base import TaskType
from src.llm.router import build_provider
from src.userbot import get_active_telethon_client


logger = logging.getLogger(__name__)

router = Router(name="draft_actions")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


# variant_groups: hash -> (timestamp, peer_id, contact_name, incoming_text, variant_dicts)
_variant_groups: dict[str, tuple[float, int, str, str, list[dict]]] = {}
_variant_lock = asyncio.Lock()
DRAFT_TTL_SECONDS = 30 * 60  # 30 минут


async def _draft_cleanup() -> None:
    """Удаляет варианты старше DRAFT_TTL_SECONDS."""
    async with _variant_lock:
        now = time.time()
        stale_v = [
            k for k, v in _variant_groups.items() if now - v[0] > DRAFT_TTL_SECONDS
        ]
        for k in stale_v:
            del _variant_groups[k]


# ── Редактировать ──


@router.callback_query(F.data.startswith("draft:edit:"))
async def cb_draft_edit(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) == 4:
        # Old format: draft:edit:{peer_id}:{hash} — single draft edit
        try:
            peer_id = int(parts[2])
        except ValueError:
            await callback.answer("Неверный формат", show_alert=True)
            return
        draft_hash = parts[3]
        await state.set_state(DraftStates.waiting_edit)
        await state.set_data({"peer_id": peer_id, "draft_hash": draft_hash})
        if callback.message:
            await callback.message.answer(
                "Пришли новый текст черновика для отправки. /cancel — отмена."
            )
    elif len(parts) == 3:
        # New format: draft:edit:{group_hash} — variant group edit
        group_hash = parts[2]
        async with _variant_lock:
            group_data = _variant_groups.get(group_hash)
        if group_data is None:
            await callback.answer("Черновик устарел или не найден", show_alert=True)
            return
        _ts, peer_id, contact_name, _incoming_text, _variants = group_data
        await state.set_state(DraftStates.waiting_edit)
        await state.set_data(
            {
                "peer_id": peer_id,
                "draft_hash": group_hash,
                "draft_variants": True,
            }
        )
        if callback.message:
            await callback.message.answer(
                f"Пришли новый текст для отправки контакту {contact_name}. /cancel — отмена."
            )
    else:
        await callback.answer("Неверный формат", show_alert=True)
        return
    await callback.answer()


@router.message(DraftStates.waiting_edit)
async def step_draft_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    peer_id = data.get("peer_id")
    if peer_id is None:
        await message.answer("Ошибка состояния. Начни заново.")
        await state.clear()
        return
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
        try:
            entity = await client.get_entity(peer_id)
            await client.send_message(entity=entity, message=new_text)
            after_kb = smart_post_action_keyboard("edit", {"peer_id": str(peer_id)})
            await message.answer("✅ Отправлено! 🚀", reply_markup=after_kb)
        except Exception as e:
            try:
                await message.answer(
                    f"❌ Ошибка отправки 😞: {sanitize_html(safe_str(e))}"
                )
            except Exception:
                logger.debug(
                    "Failed to send error message in step_draft_edit", exc_info=True
                )
    finally:
        await state.clear()


# ── Variant group storage ────────────────────────────────────────────────


async def store_variant_group(
    peer_id: int, contact_name: str, incoming_text: str, variants: list[dict]
) -> str:
    """Сохраняет группу вариантов и возвращает hash для callback'ов."""
    raw = f"{peer_id}:{contact_name}:{incoming_text}:{variants!s}"
    group_hash = hashlib.sha256(raw.encode(), usedforsecurity=False).hexdigest()[:8]
    async with _variant_lock:
        _variant_groups[group_hash] = (
            time.time(),
            peer_id,
            contact_name,
            incoming_text,
            variants,
        )
    await _draft_cleanup()
    return group_hash


def build_variants_keyboard(
    group_hash: str, variants: list[dict]
) -> InlineKeyboardMarkup:
    """Строит inline-клавиатуру для выбора варианта."""
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{i}️⃣ {v['tone']}", callback_data=f"draft:choose:{group_hash}:{i}"
            )
        ]
        for i, v in enumerate(variants, 1)
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                text="🔄 Улучшить", callback_data=f"draft:improve:{group_hash}"
            ),
            InlineKeyboardButton(
                text="✏️ Править", callback_data=f"draft:edit:{group_hash}"
            ),
            InlineKeyboardButton(text="❌ Отмена", callback_data="draft:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Choose variant ───────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("draft:choose:"))
async def cb_draft_choose(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    # draft:choose:{hash}:{idx}
    if len(parts) < 4:
        await callback.answer("Неверный формат данных", show_alert=True)
        return
    group_hash = parts[2]
    try:
        idx = int(parts[3]) - 1  # convert to 0-based
    except ValueError:
        await callback.answer("Неверный индекс", show_alert=True)
        return

    async with _variant_lock:
        group_data = _variant_groups.get(group_hash)
    if group_data is None:
        await callback.answer("Черновик устарел или не найден", show_alert=True)
        return
    ts, peer_id, _contact_name, _incoming_text, variants = group_data
    if time.time() - ts > DRAFT_TTL_SECONDS:
        await callback.answer("Черновик устарел", show_alert=True)
        return
    if idx < 0 or idx >= len(variants):
        await callback.answer("Неверный вариант", show_alert=True)
        return

    draft_text = variants[idx]["text"]
    client = get_active_telethon_client(callback.from_user.id)
    if client is None:
        await callback.answer("Нет активной сессии. Сначала /login.", show_alert=True)
        return

    try:
        entity = await client.get_entity(peer_id)
        await client.send_message(entity=entity, message=draft_text)
        async with _variant_lock:
            _variant_groups.pop(group_hash, None)
        if callback.message:
            after_kb = smart_post_action_keyboard("send", {"peer_id": str(peer_id)})
            await callback.message.edit_text("✅ Отправлено! 🚀", reply_markup=after_kb)
    except ValueError as e:
        if callback.message:
            await callback.message.edit_text(
                f"❌ Ошибка 😞: {sanitize_html(safe_str(e))}"
            )
            await callback.answer()
        else:
            await callback.answer(f"❌ Ошибка: {safe_str(e)}", show_alert=True)
        return
    except FloodWaitError as e:
        if callback.message:
            await callback.message.edit_text(f"❌ Flood wait ⏳: {e.seconds}с")
            await callback.answer()
        else:
            await callback.answer(f"❌ Flood wait: {e.seconds}с", show_alert=True)
        return
    except Exception as e:
        await callback.answer(f"❌ Ошибка отправки: {safe_str(e)}", show_alert=True)
        return
    await callback.answer()


# ── Improve variant ──────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("draft:improve:"))
async def cb_draft_improve(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверный формат", show_alert=True)
        return
    group_hash = parts[2]

    async with _variant_lock:
        group_data = _variant_groups.get(group_hash)
    if group_data is None:
        await callback.answer("Черновик устарел", show_alert=True)
        return
    _ts, peer_id, contact_name, incoming_text, _old_variants = group_data

    if callback.message is None:
        await callback.answer("Сообщение недоступно.")
        return
    await callback.message.edit_text("🔄 Улучшаю черновики…")
    await callback.answer()

    async with get_session() as session:
        owner = await _get_or_create_user(session, callback.from_user.id)
        provider = await build_provider(session, owner, task_type=TaskType.DRAFT)
    if provider is None:
        await callback.answer("Не задан LLM-ключ.", show_alert=True)
        return
    try:
        enriched_text = f"{incoming_text}\n\n(сделай ответ живее, естественнее, как в разговорной речи)"
        variants = await draft_variants(provider, contact_name, enriched_text)

        if not variants or len(variants) < 2:
            from src.agents.draft_agent import draft

            single = await draft(provider, contact_name, enriched_text)
            variants = [{"tone": "черновик", "text": single["draft"]}]

        async with _variant_lock:
            _variant_groups.pop(group_hash, None)
        new_hash = await store_variant_group(
            peer_id, contact_name, incoming_text, variants
        )

        lines = [f"🤖 <b>Улучшенные черновики для {contact_name}:</b>\n"]
        for i, v in enumerate(variants, 1):
            lines.append(f"{i}️⃣ <b>{v['tone'].capitalize()}:</b> {v['text']}")

        html = "\n".join(lines)
        kb = build_variants_keyboard(new_hash, variants)

        await callback.message.edit_text(html, reply_markup=kb)
    finally:
        try:
            await provider.close()
        except Exception:
            logger.debug("Failed to close provider in cb_draft_improve", exc_info=True)


# ── Cancel variants ──────────────────────────────────────────────────────


@router.callback_query(F.data == "draft:cancel")
async def cb_draft_cancel(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text("❌ Отменено")
    await callback.answer()
