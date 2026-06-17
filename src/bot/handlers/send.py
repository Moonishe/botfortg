import asyncio
import json
import logging
import re
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.core.infra.rate_limiter import check_rate_limit
from src.bot.handlers.free_text_common import _confirm_keyboard
from src.bot.handlers.smart_keyboard import smart_post_action_keyboard
from src.core.contacts.contact_resolver import ContactCandidate, resolve
from src.core.contacts.send_guard import build_send_guard
from src.core.security import approval
from src.core.infra.text_sanitizer import sanitize_html
from src.db.repo import (
    create_pending_action,
    delete_pending_action,
    get_contact,
    get_or_create_user,
    get_pending_action,
    is_pending_action_expired,
    verify_pending_action_hmac,
)
from src.db.session import get_session
from src.llm.base import ChatMessage
from src.llm.base import TaskType
from src.llm.router import build_provider
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)
router = Router(name="send")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())

# ── Per-user confirm-send-delete critical-section lock ─────────────────
# Prevents double-send race: two concurrent callbacks for the same user
# could both see a valid PendingAction before either deletes it.
# ponytail: dict never evicted, bounded by unique telegram_id count;
#           add TTL-based cleanup if per-process memory matters.
_confirm_locks: dict[int, asyncio.Lock] = {}
_confirm_locks_last_used: dict[int, float] = {}
_confirm_locks_lock = asyncio.Lock()
_CONFIRM_LOCK_TTL_SEC = 300  # 5 minutes


async def _get_confirm_lock(telegram_id: int) -> asyncio.Lock:
    """Return a per-user lock; serialize creation to avoid duplicate locks."""
    now = time.monotonic()
    async with _confirm_locks_lock:
        _cleanup_confirm_locks(now)
        lock = _confirm_locks.get(telegram_id)
        if lock is None:
            lock = asyncio.Lock()
            _confirm_locks[telegram_id] = lock
        _confirm_locks_last_used[telegram_id] = now
        return lock


def _cleanup_confirm_locks(now: float) -> None:
    """Remove stale locks that are not currently held."""
    stale = [
        tid
        for tid, ts in _confirm_locks_last_used.items()
        if now - ts > _CONFIRM_LOCK_TTL_SEC
    ]
    for tid in stale:
        lock = _confirm_locks.get(tid)
        if lock is None or not lock.locked():
            _confirm_locks.pop(tid, None)
            _confirm_locks_last_used.pop(tid, None)


class SendStates(StatesGroup):
    waiting_edit = State()


PARSE_SYSTEM = (
    "Тебе дают свободную фразу-инструкцию вида «скажи Оле, что созвон в 8».\n"
    "Извлеки получателя и текст сообщения. Сообщение должно быть готово к отправке "
    "(в первом лице, без префиксов «передай», «скажи»).\n\n"
    'Возвращай ТОЛЬКО JSON: {"recipient": "...", "message": "..."}.\n'
    "Если не удаётся определить — верни поля null."
)


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        return {}


def _candidates_keyboard(candidates: list[ContactCandidate], message_text: str):
    """Кнопки выбора получателя для send. callback_data: send:pick:<peer_id>

    Action создаётся уже после выбора, поэтому здесь храним сообщение в коротком кэше через FSM-data."""
    kb = InlineKeyboardBuilder()
    for c in candidates:
        kb.row(
            InlineKeyboardButton(
                text=f"{c.label()} · {c.score}",
                callback_data=f"send:pick:{c.peer_id}",
            )
        )
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="send:cancel:0"))
    return kb.as_markup()


@router.message(Command("send"))
async def cmd_send(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    # ── Rate-limit ────────────────────────────────────────────────────
    if not await check_rate_limit(message.from_user.id, window=5, max_requests=10):
        await message.answer("Слишком часто. Подожди.")
        return

    client = userbot_manager.get_client(message.from_user.id)
    if client is None:
        await message.answer("Сначала /login.")
        return
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Использование: <code>/send скажи Оле, что созвон в 8</code>\n"
            "Или: <code>/send @username | текст сообщения</code>"
        )
        return

    recipient_query: str | None = None
    text: str | None = None

    if "|" in raw:
        parts = raw.split("|", 1)
        recipient_query = parts[0].strip()
        text = parts[1].strip()

    owner = None
    if not recipient_query or not text:
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            provider = await build_provider(session, owner, task_type=TaskType.DEFAULT)
        if provider is None:
            await message.answer(
                "Нужен LLM-ключ для NL-парсинга. Добавь в /settings или используй формат «получатель | текст»."
            )
            return
        try:
            parsed_raw = await provider.chat(
                [
                    ChatMessage(role="system", content=PARSE_SYSTEM),
                    ChatMessage(role="user", content=raw),
                ],
                task_type=TaskType.DEFAULT,
            )
            parsed = _parse_json(parsed_raw)
            recipient_query = parsed.get("recipient") or recipient_query
            text = parsed.get("message") or text
        finally:
            try:
                await provider.close()
            except Exception:
                logger.debug("Failed to close provider in cmd_send", exc_info=True)

    if not recipient_query or not text:
        await message.answer(
            "Не удалось разобрать запрос. Попробуй формат: <code>/send Оля | текст</code>."
        )
        return

    if owner is None:
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
    candidates = await resolve(client, owner, recipient_query)
    if not candidates:
        await message.answer(
            sanitize_html(
                f"Не нашёл контакт «{recipient_query}». Запусти /sync и попробуй снова."
            )
        )
        return

    if len(candidates) == 1 or candidates[0].score >= 90:
        await _create_and_confirm(
            message,
            owner_telegram_id=message.from_user.id,
            peer_id=candidates[0].peer_id,
            text=text,
            label=candidates[0].label(),
        )
        return

    await state.set_data({"send_text": text})
    await message.answer(
        sanitize_html(f"Кому именно отправить «<i>{text[:80]}</i>»?"),
        reply_markup=_candidates_keyboard(candidates, text),
    )


async def _create_and_confirm(
    message: Message,
    *,
    owner_telegram_id: int,
    peer_id: int,
    text: str,
    label: str,
) -> None:
    payload_dict = {"peer_id": peer_id, "text": text}
    payload = json.dumps(payload_dict, ensure_ascii=False)
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        action = await create_pending_action(
            session,
            user_id=owner.id,
            kind="send_message",
            payload=payload,
            route="db",
            verb="send",
            risk="high",
            human_summary=f"Отправить сообщение {label}: {text[:80]}",
        )
    # Week 2: unified HMAC computed by the repository.
    sig = action.hmac_signature
    if not sig:
        logger.error("Pending action %s created with empty HMAC signature", action.id)
        await message.answer(
            sanitize_html("❌ Не удалось создать подтверждаемое действие."),
            reply_markup=None,
        )
        return
    guard_hint = ""
    try:
        guard = await build_send_guard(owner_telegram_id, peer_id, text)
        if guard.formatted_html:
            guard_hint = "\n\n" + guard.formatted_html
    except Exception:
        logger.warning("send guard failed", exc_info=True)

    await message.answer(
        sanitize_html(
            f"🤔 <b>Готов отправить</b>\n\n→ <b>Кому:</b> {label}\n→ <b>Текст:</b>\n{text}{guard_hint}\n\n<i>Подтверди отправку 👇</i>"
        ),
        reply_markup=_confirm_keyboard(action.id, sig),
    )


@router.callback_query(F.data.startswith("send:pick:"))
async def cb_pick(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    try:
        peer_id = int(parts[2])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    data = await state.get_data()
    text = data.get("send_text")
    if not text:
        await callback.answer("Сессия потеряна, попробуй /send заново", show_alert=True)
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        contact = await get_contact(session, owner, peer_id)
        label = contact.display_name if contact else str(peer_id)
        payload_dict = {"peer_id": peer_id, "text": text}
        payload = json.dumps(payload_dict, ensure_ascii=False)
        action = await create_pending_action(
            session,
            user_id=owner.id,
            kind="send_message",
            payload=payload,
            route="db",
            verb="send",
            risk="high",
            human_summary=f"Отправить сообщение {label}: {text[:80]}",
        )
    sig = action.hmac_signature
    if not sig:
        logger.error("Pending action %s created with empty HMAC signature", action.id)
        await callback.answer(
            "❌ Не удалось создать подтверждаемое действие.", show_alert=True
        )
        return
    guard_hint = ""
    try:
        guard = await build_send_guard(callback.from_user.id, peer_id, text)
        if guard.formatted_html:
            guard_hint = "\n\n" + guard.formatted_html
    except Exception:
        logger.warning("send guard failed", exc_info=True)

    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            sanitize_html(
                f"🤔 <b>Готов отправить</b>\n\n"
                f"→ <b>Кому:</b> {label}\n"
                f"→ <b>Текст:</b>\n{text}{guard_hint}"
            ),
            reply_markup=_confirm_keyboard(action.id, sig),
        )
    await callback.answer()


@router.callback_query(
    F.data.startswith("send:cancel:") | F.data.startswith("ap:cancel:send:")
)
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel a pending send action. Accepts legacy send:cancel: and new ap:cancel:send:."""
    data = callback.data or ""
    action_id = 0
    try:
        if data.startswith("ap:cancel:send:"):
            parsed = approval.parse_cancel_callback(data)
            if parsed:
                action_id = int(parsed[1])
        else:
            parts = data.split(":")
            action_id = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        action_id = 0

    if action_id:
        # Prevent race with cb_confirm: if confirm is in-flight and has
        # already validated the action, cancel may still delete it before
        # the message is sent — resulting in a message being sent AFTER
        # the user clicked cancel. Serialize via the per-user confirm lock.
        lock = await _get_confirm_lock(callback.from_user.id)
        async with lock, get_session() as session:
            user = await get_or_create_user(session, callback.from_user.id)
            await delete_pending_action(session, action_id, user)
    await state.clear()
    if callback.message:
        await callback.message.edit_text("❌ Отправка отменена. 🚫")
    await callback.answer()


@router.callback_query(F.data.startswith("send:edit:"))
async def cb_edit(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    try:
        action_id = int(parts[2])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    await state.set_state(SendStates.waiting_edit)
    await state.set_data({"action_id": action_id})
    if callback.message:
        await callback.message.answer("Введи новый текст сообщения. /cancel — отмена.")
    await callback.answer()


@router.message(SendStates.waiting_edit)
async def step_edit(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw in ("/cancel", "/back", "/menu"):
        await state.clear()
        await message.answer("🚫 Редактирование отменено.")
        return
    if raw.startswith("/"):
        return  # let command handlers process
    new_text = raw
    if not new_text:
        await message.answer("Пустой текст. Повтори или /cancel.")
        return
    data = await state.get_data()
    action_id = data.get("action_id")
    async with get_session() as session:
        user = await get_or_create_user(session, message.from_user.id)
        action = await get_pending_action(session, action_id, user)
        if action is None:
            await state.clear()
            await message.answer("Сессия отправки потеряна. Запусти /send заново.")
            return
        try:
            payload = json.loads(action.payload)
            peer_id = payload["peer_id"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(
                "step_edit: corrupt payload for action_id=%d: %s", action_id, e
            )
            await state.clear()
            await message.answer("Ошибка данных действия. Запусти /send заново.")
            return
        # Удаляем старый PendingAction — HMAC был вычислен от старого текста
        # и больше не валиден. Создаём новый с обновлённым payload.
        await delete_pending_action(session, action_id, user)
        new_payload_dict = {"peer_id": peer_id, "text": new_text}
        new_payload = json.dumps(new_payload_dict, ensure_ascii=False)
        action = await create_pending_action(
            session,
            user_id=user.id,
            kind="send_message",
            payload=new_payload,
            route="db",
            verb="send",
            risk="high",
            human_summary=f"Отправить сообщение: {new_text[:80]}",
        )
        contact = await get_contact(session, user, peer_id)
        label = contact.display_name if contact else str(peer_id)
    guard_hint = ""
    try:
        guard = await build_send_guard(message.from_user.id, peer_id, new_text)
        if guard.formatted_html:
            guard_hint = "\n\n" + guard.formatted_html
    except Exception:
        logger.warning("send guard failed", exc_info=True)

    sig = action.hmac_signature
    if not sig:
        logger.error("Pending action %s created with empty HMAC signature", action.id)
        await message.answer(
            sanitize_html("❌ Не удалось создать подтверждаемое действие."),
            reply_markup=None,
        )
        return
    await state.clear()
    await message.answer(
        sanitize_html(
            f"🤔 <b>Готов отправить</b>\n\n→ <b>Кому:</b> {label}\n→ <b>Текст:</b>\n{new_text}{guard_hint}"
        ),
        reply_markup=_confirm_keyboard(action.id, sig),
    )


@router.callback_query(F.data.startswith("ap:send:"))
async def cb_confirm(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    """Confirm and execute a pending send action. Only unified ap:send: format accepted."""
    data = callback.data or ""
    parsed = approval.parse_callback(data)
    if parsed is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    _, action_key, callback_hmac = parsed
    try:
        action_id = int(action_key)
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    client = userbot_manager.get_client(callback.from_user.id)
    if client is None:
        await callback.answer("Сначала /login", show_alert=True)
        return

    lock = await _get_confirm_lock(callback.from_user.id)
    try:
        # Wrap in a timeout; if the lock is held by a crashed/dead coroutine,
        # this prevents permanent deadlock for the user.
        async with asyncio.timeout(_CONFIRM_LOCK_TTL_SEC):
            async with lock:
                async with get_session() as session:
                    user = await get_or_create_user(session, callback.from_user.id)
                    action = await get_pending_action(session, action_id, user)
                    if action is None:
                        await callback.answer(
                            "❌ Действие не найдено или уже выполнено.",
                            show_alert=True,
                        )
                        return

                    # Проверка TTL
                    if is_pending_action_expired(action):
                        await callback.answer(
                            "❌ Срок действия истёк.", show_alert=True
                        )
                        logger.info("cb_confirm: expired action_id=%d", action_id)
                        return

                    # HMAC verification — always enforced.
                    if not verify_pending_action_hmac(action, callback_hmac):
                        await callback.answer(
                            "❌ Недействительная подпись.", show_alert=True
                        )
                        logger.warning(
                            "cb_confirm: HMAC mismatch for action_id=%d", action_id
                        )
                        return

                    try:
                        payload = json.loads(action.payload)
                        peer_id = payload["peer_id"]
                        text = payload["text"]
                    except (json.JSONDecodeError, KeyError, TypeError) as e:
                        logger.warning(
                            "cb_confirm: corrupt payload for action_id=%d: %s",
                            action_id,
                            e,
                        )
                        await callback.answer(
                            "❌ Ошибка данных действия.", show_alert=True
                        )
                        return

                    # Defensive: reject null or empty text (corrupt payload edge case)
                    if not text:
                        await callback.answer(
                            "❌ Текст сообщения пуст.", show_alert=True
                        )
                        logger.warning(
                            "cb_confirm: empty text in payload for action_id=%d",
                            action_id,
                        )
                        return

                # Send Guard — предупреждение перед отправкой
                # ponytail: try/except mirrors _create_and_confirm, cb_pick, step_edit;
                # guard failure is non-fatal — message should still be sent.
                guard = None
                try:
                    guard = await build_send_guard(callback.from_user.id, peer_id, text)
                except Exception:
                    logger.warning("send guard failed in cb_confirm", exc_info=True)
                if guard and guard.warnings:
                    await callback.answer(
                        f"⚠️ {guard.warnings[0][:100]}", show_alert=True
                    )
                    return

                try:
                    entity = await client.get_entity(peer_id)
                    await client.send_message(entity, text)
                except Exception as e:
                    logger.warning("send_message failed: %s", e)
                    await callback.answer(
                        "❌ Ошибка отправки. Попробуй ещё раз.", show_alert=True
                    )
                    if callback.message:
                        await callback.message.edit_text(
                            sanitize_html(
                                "❌ Не удалось отправить сообщение. Попробуй ещё раз"
                            )
                        )
                    return

                # M-44: удаляем PendingAction только после успешной отправки.
                # Cleanup failure is non-fatal — the message was already sent.
                label = str(peer_id)
                try:
                    async with get_session() as session:
                        await delete_pending_action(session, action_id, user)
                        contact = await get_contact(session, user, peer_id)
                        if contact:
                            label = contact.display_name
                except Exception:
                    logger.warning(
                        "delete_pending_action or contact lookup failed after "
                        "successful send (action_id=%d)",
                        action_id,
                        exc_info=True,
                    )
    except TimeoutError:
        # The lock was held for too long — likely a crashed/dead coroutine
        # from a prior callback. Replace the stuck lock so future attempts
        # don't also time out.
        async with _confirm_locks_lock:
            if _confirm_locks.get(callback.from_user.id) is lock:
                _confirm_locks[callback.from_user.id] = asyncio.Lock()
                _confirm_locks_last_used[callback.from_user.id] = time.monotonic()
        logger.warning(
            "cb_confirm: lock timeout for user %d — replaced stuck lock",
            callback.from_user.id,
        )
        await callback.answer(
            "❌ Система занята, попробуй через минуту.", show_alert=True
        )
        return

    text_for_display = str(text or "")
    snippet = text_for_display[:60]
    if len(text_for_display) > 60:
        snippet += "…"

    after_kb = smart_post_action_keyboard("send", {"peer_id": str(peer_id)})

    if callback.message:
        await callback.message.edit_text(
            sanitize_html(f"✅ Отправлено «{label}»: {snippet}"), reply_markup=after_kb
        )
    await callback.answer("Отправлено")


@router.callback_query(F.data.startswith("send:again:"))
async def cb_send_again(callback: CallbackQuery) -> None:
    await callback.answer("Открой меню отправки")
    if callback.message:
        await callback.message.edit_text(
            "✏️ Используй /chat или отправь сообщение напрямую чтобы написать ещё.",
            reply_markup=smart_post_action_keyboard("general"),
        )
