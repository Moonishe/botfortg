"""Зеркало всех сообщений (входящих и исходящих) в БД и FTS5 в реальном времени.
Транскрипция голоса и парсинг документов — лениво в момент анализа.
Также отслеживает реакции пользователей на сообщения (включая сообщения бота)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.tl.custom import Message as TgMessage
from telethon.tl.types import User as TgUser

from src.core.infra.task_manager import track_ff
from src.core.scheduling.notification_queue import notification_queue
from src.core.infra.notifier import notifier
from src.db.repo import (
    get_contact,
    get_or_create_user,
    get_watched_peers,
    save_reaction,
    upsert_contact,
    upsert_conversation_state,
    upsert_message,
)
from src.db.session import get_session
from src.llm.base import TaskType
from src.llm.router import build_provider


logger = logging.getLogger(__name__)

# Semaphore to limit concurrent background inbox processing tasks
_bg_semaphore = asyncio.Semaphore(50)


def _classify(msg: TgMessage) -> str:
    if msg.voice:
        return "voice"
    if msg.audio:
        return "audio"
    if msg.document:
        return "document"
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.sticker:
        return "sticker"
    if msg.video_note:
        return "video_note"
    if msg.poll:
        return "poll"
    if msg.geo:
        return "geo"
    if msg.venue:
        return "venue"
    if msg.contact:
        return "contact"
    if msg.game:
        return "game"
    if msg.invoice:
        return "invoice"
    if msg.text:
        return "text"
    return "other"


def _peer_id_of(msg: TgMessage) -> int | None:
    chat = msg.chat
    if chat is not None:
        return chat.id
    if msg.peer_id is not None and hasattr(msg.peer_id, "user_id"):
        return msg.peer_id.user_id
    return msg.chat_id


async def _sender_label(msg: TgMessage) -> str | None:
    if msg.out:
        return None  # это мы сами
    try:
        sender = await msg.get_sender()
    except Exception:
        sender = None
    if sender is None:
        return None
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join(p for p in parts if p).strip()
    if name:
        return name
    return getattr(sender, "username", None) or str(sender.id)


async def _process_incoming_bg(
    owner_telegram_id: int,
    peer_id: int,
    sender_name: str,
    text: str,
    *,
    is_private: bool = True,
) -> None:
    """Фоновая обработка входящего сообщения: InboxManager + notifier.

    Открывает собственную сессию БД, не роняет обработчик при ошибках.
    """
    from src.core.actions.inbox_manager import InboxAction, process_incoming

    async with _bg_semaphore:
        try:
            async with get_session() as _im_session:
                _im_owner = await get_or_create_user(_im_session, owner_telegram_id)
                _im_contact = await get_contact(_im_session, _im_owner, peer_id)
                _im_provider = await build_provider(
                    _im_session, _im_owner, task_type=TaskType.CLASSIFY
                )
                if _im_provider is None:
                    return
                decision = await process_incoming(
                    message_text=text,
                    sender_name=sender_name,
                    peer_id=peer_id,
                    owner=_im_owner,
                    contact=_im_contact,
                    provider=_im_provider,
                    is_private=is_private,
                )

                # Обновить ConversationState
                status = "active"
                if decision.action == InboxAction.QUEUE_FOR_DIGEST:
                    status = "waiting_reply"
                await upsert_conversation_state(
                    _im_session,
                    _im_owner,
                    peer_id,
                    status=status,
                    increment_unread=True,
                    last_incoming_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )

            # Применить решение (вне сессии)
            if decision.action == InboxAction.NOTIFY_URGENT:
                await notifier.notify(
                    f"🔴 <b>СРОЧНОЕ от {sender_name}!</b>\n\n<i>{text[:300]}</i>"
                )
            elif decision.action == InboxAction.DRAFT_SUGGEST:
                await notification_queue.enqueue(
                    topic="inbox",
                    text=f"💬 <b>{sender_name}:</b> <i>{text[:200]}</i>\n\n→ Напиши ответ? /chat {sender_name}",
                    priority=2,
                    category="draft",
                )
            # SILENT_LOG / IGNORE — только сохранили в БД, ничего не делаем
        except Exception:
            logger.exception("Background inbox processing failed for peer %s", peer_id)


async def _save_and_process_reaction(
    owner_telegram_id: int,
    reaction_data: dict,
) -> None:
    """Сохранить реакцию в БД и обработать как feedback для памяти (fire-and-forget).

    Не роняет основной поток при ошибках — все исключения перехватываются.
    """
    try:
        from src.core.memory.reaction_feedback import process_reaction_feedback
        from src.db.models import Message as MessageModel
        from sqlalchemy import select

        async with get_session() as session:
            owner = await get_or_create_user(session, owner_telegram_id)
            chat_id = reaction_data["chat_id"]
            message_id = reaction_data["message_id"]

            # Проверяем, является ли это сообщение исходящим сообщением бота
            is_bot_message = False
            try:
                result = await session.execute(
                    select(MessageModel).where(
                        MessageModel.user_id == owner.id,
                        MessageModel.peer_id == chat_id,
                        MessageModel.message_id == message_id,
                        MessageModel.is_outgoing.is_(True),
                    )
                )
                if result.scalar_one_or_none() is not None:
                    is_bot_message = True
            except Exception:
                logger.debug(
                    "Не удалось проверить принадлежность сообщения %d боту",
                    message_id,
                )

            await save_reaction(
                session,
                user_id=owner.id,
                chat_id=chat_id,
                message_id=message_id,
                reactor_id=reaction_data["reactor_id"],
                reaction=reaction_data["reaction"],
                is_bot_message=is_bot_message,
            )

        # Если реакция на сообщение бота — корректируем confidence памяти
        if is_bot_message:
            await process_reaction_feedback(reaction_data)

    except Exception:
        logger.exception(
            "Ошибка сохранения/обработки реакции для сообщения %d",
            reaction_data.get("message_id", 0),
        )


# Защита от утечки обработчиков при переподключении.
# При повторном вызове attach_mirror на том же клиенте — не дублируем обработчик.
_attached_mirror_clients: set[int] = set()


def attach_mirror(client: TelegramClient, owner_telegram_id: int) -> None:
    client_id = id(client)
    if client_id in _attached_mirror_clients:
        logger.debug(
            "Mirror handler already attached for client %s — skipping duplicate",
            client_id,
        )
        return
    _attached_mirror_clients.add(client_id)

    async def on_message(event: events.NewMessage.Event) -> None:
        try:
            msg: TgMessage = event.message
            peer_id = _peer_id_of(msg)
            if not peer_id:
                return

            should_process_inbox = True

            # Don't run heavy processing until onboarding is complete, but still mirror messages.
            try:
                from src.core.onboarding import get_onboarding_phase

                phase = await get_onboarding_phase(owner_telegram_id)
                if phase < 4:
                    should_process_inbox = False
            except Exception:
                logger.exception("onboarding phase check failed; mirroring only")
                should_process_inbox = False

            kind = _classify(msg)
            text = msg.text or msg.message or None
            sender_name = await _sender_label(msg)

            # ===== SESSION: объединённая сессия для всех DB-операций =====
            # Раньше было две сессии: _w_session (watched_peers) и session (mirror).
            # Объединены в одну для уменьшения накладных расходов на connect/begin/commit.
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)

                # Проверка watched_peers — фильтрация чатов (было отдельной сессией)
                _watched = await get_watched_peers(session, owner)
                if _watched and peer_id not in _watched:
                    should_process_inbox = False

                # сброс статуса отсутствия при любом исходящем
                if msg.out and owner.absence_status is not None:
                    owner.absence_status = None
                    owner.absence_message = None

                try:
                    chat = await event.get_chat()
                except Exception:
                    chat = None
                if chat is not None:
                    if isinstance(chat, TgUser):
                        parts = [
                            getattr(chat, "first_name", None),
                            getattr(chat, "last_name", None),
                        ]
                        display = " ".join(p for p in parts if p).strip() or (
                            chat.username or str(peer_id)
                        )
                        await upsert_contact(
                            session,
                            owner,
                            peer_id=peer_id,
                            peer_kind="user",
                            is_bot=bool(getattr(chat, "bot", False)),
                            display_name=display,
                            username=getattr(chat, "username", None),
                            phone=getattr(chat, "phone", None),
                        )
                    else:
                        title = getattr(chat, "title", None) or str(peer_id)
                        kind_chat = (
                            "channel" if getattr(chat, "broadcast", False) else "chat"
                        )
                        await upsert_contact(
                            session,
                            owner,
                            peer_id=peer_id,
                            peer_kind=kind_chat,
                            is_bot=False,
                            display_name=title,
                            username=getattr(chat, "username", None),
                        )

                await upsert_message(
                    session,
                    user_id=owner.id,
                    peer_id=peer_id,
                    message_id=msg.id,
                    sender_id=msg.sender_id,
                    sender_name=sender_name,
                    is_outgoing=bool(msg.out),
                    date=msg.date.replace(tzinfo=None)
                    if msg.date
                    else datetime.now(timezone.utc).replace(tzinfo=None),
                    kind=kind,
                    text=text,
                    transcript=None,
                    media_path=None,
                    extracted_text=None,
                )

                # детекция фраз отсутствия в исходящих сообщениях
                if msg.out and msg.text:
                    from src.core.scheduling.absence_detector import (
                        detect_absence_phrases,
                    )

                    status, message_text = detect_absence_phrases(msg.text)
                    if status:
                        owner.absence_status = status
                        owner.absence_message = message_text or msg.text[:100]

            # ===== InboxManager: тяжёлая обработка — в фон =====
            # Skip bot senders — prevents feedback loop with control bot.
            # Use sender entity (not chat) so it works in groups too.
            # Fail-safe: if we can't determine, skip processing.
            _is_bot_sender = True  # default: skip (fail-safe)
            if msg.out:
                _is_bot_sender = False  # our own messages are not from bots
            elif msg.sender_id:
                try:
                    _sender_entity = await client.get_entity(msg.sender_id)
                    _is_bot_sender = bool(getattr(_sender_entity, "bot", False))
                except Exception:
                    _is_bot_sender = True  # can't resolve → skip (fail-safe)
            if should_process_inbox and not msg.out and msg.text and not _is_bot_sender:
                track_ff(
                    asyncio.create_task(
                        _process_incoming_bg(
                            owner_telegram_id=owner_telegram_id,
                            peer_id=peer_id,
                            sender_name=sender_name or str(peer_id),
                            text=msg.text,
                            is_private=event.is_private
                            if event.is_private is not None
                            else True,
                        )
                    )
                )
        except Exception:
            logger.exception("mirror handler failed")

    async def _on_message_edited(event: events.MessageEdited.Event) -> None:
        """Обработка редактирования сообщения — детекция реакций пользователей.

        Когда пользователь ставит реакцию на сообщение, Telegram отправляет
        событие MessageEdited с обновлённым списком реакций.
        Извлекаем recent_reactions и сохраняем в БД (fire-and-forget).
        """
        try:
            msg: TgMessage = event.message
            # Проверяем, есть ли реакции на сообщении
            if not hasattr(msg, "reactions") or not msg.reactions:
                return

            peer_id = _peer_id_of(msg)
            if not peer_id:
                return

            reactions_obj = msg.reactions
            # recent_reactions — список MessagePeerReaction (кто какую реакцию поставил)
            recent = getattr(reactions_obj, "recent_reactions", None) or []

            if not recent:
                return

            for r in recent:
                try:
                    reactor_id = getattr(r, "peer_id", None)
                    reaction_emoji = None
                    if hasattr(r, "reaction"):
                        r_obj = r.reaction
                        if hasattr(r_obj, "emoticon"):
                            reaction_emoji = r_obj.emoticon

                    if reactor_id is None or reaction_emoji is None:
                        continue

                    reaction_data = {
                        "message_id": msg.id,
                        "chat_id": peer_id,
                        "reactor_id": reactor_id,
                        "reaction": str(reaction_emoji),
                        "timestamp": datetime.now(timezone.utc),
                    }

                    # Fire-and-forget: не блокируем обработку сообщений
                    track_ff(
                        asyncio.create_task(
                            _save_and_process_reaction(
                                owner_telegram_id=owner_telegram_id,
                                reaction_data=reaction_data,
                            )
                        )
                    )
                except Exception:
                    logger.exception(
                        "Ошибка обработки отдельной реакции в чате %d", peer_id
                    )
        except Exception:
            logger.exception("reaction edited handler failed")

    client.add_event_handler(on_message, events.NewMessage())
    client.add_event_handler(_on_message_edited, events.MessageEdited())
    logger.info("Mirror handler attached for user %s", owner_telegram_id)
