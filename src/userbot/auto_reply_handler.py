"""Auto-reply event handler and public API.

Handler logic, public wrapper, and Telethon registration — separated
from context-building for maintainability.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC

from telethon import TelegramClient, events
from telethon.tl.custom import Message as TgMessage
from telethon.tl.types import User as TgUser

from src.config import settings
from src.core.contacts.auto_reply_decision import AutoReplyVerdict, decide
from src.core.infra.text_sanitizer import sanitize_html
from src.core.scheduling.notification_queue import notification_queue
from src.db.models import User
from src.db.repo import (
    add_auto_reply_log,
    get_contact,
    get_or_create_user,
    upsert_contact,
)
from src.db.session import get_session
from src.userbot.auto_reply_context import _check_and_track_offline

logger = logging.getLogger(__name__)

# Защита от утечки обработчиков при переподключении.
# При повторном вызове attach_auto_reply на том же клиенте — не дублируем обработчик.
_attached_auto_reply_clients: set[int] = set()

# TOCTOU guard: prevent duplicate auto-replies when user sends multiple messages quickly.
# decide() checks cooldown in DB, but AutoReplyLog is written AFTER LLM call (1-5s gap).
# Without this lock, 2 messages → both pass cooldown → both get replies.
# ponytail: in-memory per-peer lock, upgrade to DB flag if multi-process.
_active_reply_locks: dict[int, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()
_LOCK_CLEANUP_THRESHOLD = 200  # max locks before cleanup


async def _get_peer_lock(peer_id: int) -> asyncio.Lock:
    global _active_reply_locks
    async with _locks_guard:
        # Cleanup: if dict too large, remove only UNLOCKED entries.
        # Clearing all would break TOCTOU protection for in-flight replies.
        if len(_active_reply_locks) > _LOCK_CLEANUP_THRESHOLD:
            _active_reply_locks = {
                k: v for k, v in _active_reply_locks.items() if v.locked()
            }
        if peer_id not in _active_reply_locks:
            _active_reply_locks[peer_id] = asyncio.Lock()
        return _active_reply_locks[peer_id]


async def _make_handler(client: TelegramClient, owner_telegram_id: int):
    """Возвращает event handler, замкнутый на owner_telegram_id."""

    async def handler(event: events.NewMessage.Event) -> None:
        try:
            msg: TgMessage = event.message
            if msg.out:
                return
            if getattr(msg, "sticker", None) or getattr(msg, "gif", None):
                return  # stickers/GIFs don't need replies

            # TOCTOU guard: acquire per-peer lock before any DB check.
            # Prevents duplicate replies when multiple messages arrive quickly.
            _lock_key = (
                event.chat_id if event.chat_id is not None else event.sender_id or 0
            )
            peer_lock = await _get_peer_lock(_lock_key)
            if peer_lock.locked():
                logger.debug(
                    "Auto-reply skip: already processing for peer %s", event.chat_id
                )
                return
            async with peer_lock:
                await _handle_event(client, owner_telegram_id, event)
        except Exception:
            logger.exception("Auto-reply handler error")

    async def _handle_event(
        client: TelegramClient, owner_telegram_id: int, event: events.NewMessage.Event
    ) -> None:
        try:
            msg: TgMessage = event.message
            if msg.out:
                return
            if getattr(msg, "sticker", None) or getattr(msg, "gif", None):
                return  # stickers/GIFs don't need replies
            if not event.is_private:
                # Group/channel gating — only respond if explicitly enabled.
                # ponytail: config-based whitelist + mention check, upgrade to DB-stored per-group policy if needed.
                if not settings.userbot_group_enabled:
                    return
                group_id = event.chat_id
                allowed = settings.userbot_group_allowed_ids
                if allowed and str(group_id) not in allowed.split(","):
                    return
                if settings.userbot_group_require_mention:
                    msg_text = (msg.text or msg.message or "").lower()
                    me = await client.get_me()
                    bot_username = getattr(me, "username", None)
                    if bot_username and f"@{bot_username.lower()}" not in msg_text:
                        return
            sender = await event.get_sender()
            if not isinstance(sender, TgUser):
                return  # только от User-объектов
            is_bot = bool(getattr(sender, "bot", False))
            is_private = bool(event.is_private)

            # ── Skip bots: never auto-reply or pair with bot accounts ────
            if is_bot:
                return

            async with get_session() as session:
                owner: User = await get_or_create_user(session, owner_telegram_id)
                if owner.settings is None or not owner.settings.auto_reply_enabled:
                    return

                # запомним / обновим контакт до принятия решения
                parts = [
                    getattr(sender, "first_name", None),
                    getattr(sender, "last_name", None),
                ]
                display = " ".join(p for p in parts if p).strip() or (
                    sender.username or str(sender.id)
                )
                existing = await get_contact(session, owner, sender.id)
                await upsert_contact(
                    session,
                    owner,
                    peer_id=sender.id,
                    peer_kind="user",
                    is_bot=is_bot,
                    display_name=display,
                    username=getattr(sender, "username", None),
                    phone=getattr(sender, "phone", None),
                )

                # Folder filter (остаётся отдельно — это не про auto-reply решение,
                # а про то, какие чаты вообще мониторим)
                if (
                    owner.settings.monitor_only_selected_folders
                    and owner.settings.monitored_folders
                ):
                    import json as _ar_json

                    monitored = _ar_json.loads(owner.settings.monitored_folders)
                    if monitored:
                        contact_folders = (
                            (existing.folder_names or "").split(",") if existing else []
                        )
                        contact_folders = [
                            f.strip() for f in contact_folders if f.strip()
                        ]
                        if not any(f in monitored for f in contact_folders):
                            return

                # Определяем онлайн-статус владельца (и трекаем сон/absence)
                owner_offline = await _check_and_track_offline(client, session, owner)

                # ── Если владелец онлайн — не отвечаем вообще ──────────────
                # Auto-reply only when owner is offline/away/sleeping.
                if not owner_offline:
                    return

                # ── Garbage/spam filter: ignore very short messages silently ──
                # ponytail: was "🚫🤡 Ты в муте. Сиди." — bad UX, bot shouldn't
                # insult people. Just ignore silently.
                sender_id = sender.id
                if sender_id != settings.owner_telegram_id:
                    _msg_text = (msg.text or msg.message or "").strip()
                    if len(_msg_text) < 2 and not msg.sticker:
                        return  # ignore silently, don't reply

                # ── Единый вызов decision layer ────────────────────────────
                choice = await decide(
                    session=session,
                    owner=owner,
                    peer_id=sender.id,
                    is_private=is_private,
                    is_bot=is_bot,
                    contact=existing,
                    is_online=not owner_offline,
                    msg_text=msg.text or msg.message or "",
                )

                if choice.verdict != AutoReplyVerdict.SEND:
                    logger.debug(
                        "auto-reply skip: %s (style=%s) — %s",
                        choice.verdict.value,
                        choice.style,
                        choice.reason,
                    )
                    return

                incoming_text = msg.text or msg.message or ""
                if not incoming_text.strip():
                    return  # медиа без текста — не отвечаем автоматически

                mode = owner.settings.auto_reply_mode
                static_text = owner.settings.auto_reply_text or ""

            # ── Генерация ответа (вне сессии, может быть долгой) ──────────
            if mode == "smart":
                # ponytail: lazy import to avoid circular dependency
                # (handler → _build_reply_text in auto_reply facade)
                from src.userbot.auto_reply import _build_reply_text

                reply = await _build_reply_text(
                    owner_telegram_id,
                    sender.id,
                    display,
                    incoming_text,
                    style=choice.style,
                )
                if not reply:
                    return
                # Humanizer для smart-ответов (чтобы не звучать как AI)
                try:
                    from src.core.humanizer.humanizer import (
                        humanize_response_async,
                        record_owner_emojis,
                    )

                    # H2: Track owner's emoji preferences from incoming message
                    record_owner_emojis(owner_telegram_id, incoming_text)

                    reply = await humanize_response_async(
                        reply or "",
                        user_message_len=len(incoming_text),
                    )
                except Exception:
                    logger.debug("Non-critical error", exc_info=True)

            else:  # static (default)
                reply = static_text.strip()
                if not reply:
                    return

            # ── SendGuard: check for toxic contacts, negative memories ──
            # Prevents auto-replying to contacts with known risks.
            try:
                from src.core.contacts.send_guard import build_send_guard

                guard = await build_send_guard(owner_telegram_id, sender.id, reply)
                if guard.risk_level == "high":
                    logger.warning(
                        "Auto-reply BLOCKED by SendGuard (risk=high) for "
                        "contact %s: %s",
                        sender.id,
                        "; ".join(guard.warnings[:2]),
                    )
                    return
            except Exception:
                logger.debug("SendGuard check failed (non-critical)", exc_info=True)

            # A2: Natural response delay — simulate reading + typing time.
            # ponytail: simple heuristic, upgrade to WPM-based if realism matters.
            _msg_len = len(incoming_text)
            if _msg_len < 50:
                _delay = 1.5  # short message — quick reply
            elif _msg_len < 200:
                _delay = 3.0  # medium — read + think
            else:
                _delay = 5.0  # long — takes time to read
            await asyncio.sleep(_delay)

            await event.respond(reply)

            # NOTE: global rate-limit slot is now reserved atomically inside
            # auto_reply_decision.decide() via _global_reply_reserve(). The
            # previous separate _global_reply_increment() call would double-
            # count and also re-open the TOCTOU window that reserve() closes.

            # ponytail: consolidated 2 DB sessions into 1 (was: 2 separate
            # async with get_session() blocks for ConversationState + log)
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)
                from src.db.repo import upsert_conversation_state

                await upsert_conversation_state(
                    session,
                    owner,
                    sender.id,
                    status="active",
                    last_outgoing_at=datetime.now(UTC).replace(tzinfo=None),
                    last_auto_reply_at=datetime.now(UTC).replace(tzinfo=None),
                )
                await add_auto_reply_log(
                    session,
                    user_id=owner.id,
                    peer_id=sender.id,
                    peer_name=display,
                    incoming_text=incoming_text[:500],
                    reply_text=reply,
                )

            await notification_queue.enqueue(
                topic="auto_reply",
                text=f"🤖 <b>Авто-ответ</b> для <b>{sanitize_html(display)}</b>\n\n"
                f"<i>Им:</i> {sanitize_html(incoming_text[:200])}\n"
                f"<i>Я:</i> {sanitize_html(reply)}",
                priority=2,
                category="auto_reply",
            )

            # S7: Auto-contact profiling — update style profile after dialog.
            # ponytail: best-effort, non-blocking, 1 LLM call per auto-reply.
            try:
                from src.core.contacts.style_profile import (
                    update_style_profile_for_contact,
                )
                from src.llm.router import build_provider
                from src.llm.base import TaskType

                async with get_session() as _prof_session:
                    _prof_owner = await get_or_create_user(
                        _prof_session, owner_telegram_id
                    )
                    _prof_provider = await build_provider(
                        _prof_session,
                        _prof_owner,
                        purpose="background",
                        task_type=TaskType.SUMMARIZE,
                    )
                if _prof_provider:
                    await update_style_profile_for_contact(
                        _prof_provider, owner_telegram_id, sender.id, sample_size=20
                    )
                    await _prof_provider.close()
            except Exception:
                logger.debug("S7 auto-contact profiling failed", exc_info=True)
        except Exception:
            logger.exception("auto-reply handler failed")

    return handler


async def generate_smart_reply(
    client: TelegramClient,
    owner_telegram_id: int,
    peer_id: int,
    sender_name: str,
    incoming_text: str,
    style: str = "default",
) -> str | None:
    """Публичная обёртка для генерации умного авто-ответа.

    Вызывается из InboxManager или напрямую из других модулей.
    Возвращает сгенерированный текст или None."""
    # ponytail: lazy import to avoid circular dependency
    from src.userbot.auto_reply import _build_reply_text

    try:
        return await _build_reply_text(
            owner_telegram_id=owner_telegram_id,
            peer_id=peer_id,
            sender_name=sender_name,
            incoming_text=incoming_text,
            style=style,
        )
    except Exception:
        logger.exception("generate_smart_reply failed")
        return None


def attach_auto_reply(client: TelegramClient, owner_telegram_id: int) -> None:
    client_id = id(client)
    if client_id in _attached_auto_reply_clients:
        logger.debug(
            "Auto-reply handler already attached for client %s — skipping duplicate",
            client_id,
        )
        return
    _attached_auto_reply_clients.add(client_id)

    _handler_cache = None

    async def _wrapper(event):
        nonlocal _handler_cache
        if _handler_cache is None:
            _handler_cache = await _make_handler(client, owner_telegram_id)
        await _handler_cache(event)

    client.add_event_handler(_wrapper, events.NewMessage(incoming=True))
    logger.info("Auto-reply handler attached for user %s", owner_telegram_id)
