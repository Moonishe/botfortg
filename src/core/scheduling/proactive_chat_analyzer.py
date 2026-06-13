"""Проактивный анализатор чатов (лёгкая версия).

Запускается раз в 6 часов, проверяет самые активные контакты
и делает краткий анализ для тех, где есть новые сообщения.

Не требует изменений в БД — использует существующие Contact-поля.

Лимит: макс 2 контакта за запуск, только если >20 новых сообщений.
"""

import asyncio
import logging
from datetime import datetime, timedelta, UTC
from functools import partial

from src.config import settings
from src.core.infra.task_manager import task_manager
from src.core.scheduling.notification_queue import notification_queue
from src.db.models import Message
from src.db.repo import get_or_create_user, list_contacts
from src.db.session import get_session
from src.llm.base import TaskType
from src.llm.router import build_provider
from src.core.infra.userbot_gateway import get_userbot_gateway

logger = logging.getLogger(__name__)

_overlap_guard = asyncio.Lock()

MAX_CONTACTS = 2
INTERVAL_HOURS = 6
MIN_NEW_MESSAGES = 20


async def _proactive_scan(telegram_id: int) -> None:
    """Тихий фоновый анализ самых активных чатов."""
    client = get_userbot_gateway().get_client(telegram_id)
    if client is None:
        return

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, telegram_id)
            provider = await build_provider(session, owner, task_type=TaskType.CLASSIFY)
            if provider is None:
                return
            all_contacts = await list_contacts(
                session, owner, kinds=("user",), include_bots=False
            )

        # Выбираем контакты с наибольшим числом входящих сообщений за 24ч
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        peer_ids = [c.peer_id for c in all_contacts]
        if not peer_ids:
            return

        # Один агрегированный запрос вместо N отдельных
        async with get_session() as session:
            from sqlalchemy import select, func

            stmt = (
                select(Message.peer_id, func.count(Message.id))
                .where(
                    Message.user_id == owner.id,
                    Message.peer_id.in_(peer_ids),
                    Message.date >= cutoff,
                    Message.is_outgoing == False,
                )
                .group_by(Message.peer_id)
            )
            rows = (await session.execute(stmt)).all()
            counts = {row[0]: row[1] for row in rows}

        # Сортируем по активности, отсекаем < MIN_NEW_MESSAGES
        active = sorted(
            [
                (counts[c.peer_id], c)
                for c in all_contacts
                if counts.get(c.peer_id, 0) >= MIN_NEW_MESSAGES
            ],
            key=lambda x: x[0],
            reverse=True,
        )[:MAX_CONTACTS]

        # ── Параллельный анализ контактов с семафором (макс. 2 одновременных LLM-вызова) ──
        _proactive_analysis_sem = asyncio.Semaphore(2)

        async def _analyze_one(contact, msg_count: int) -> None:
            """Анализирует один контакт (с обработкой ошибок)."""
            async with _proactive_analysis_sem:
                try:
                    await _analyze_contact(
                        contact, msg_count, client, provider, owner, telegram_id
                    )
                except Exception:
                    logger.warning(
                        "proactive scan skip %s", contact.display_name, exc_info=True
                    )

        await asyncio.gather(
            *[_analyze_one(contact, msg_count) for msg_count, contact in active],
            return_exceptions=True,
        )

        # ── Проверка необходимости авто-саммари для активных чатов ──
        from src.core.memory.chat_summarizer import check_chat_needs_summary

        for _msg_count, contact in active:
            try:
                summary_info = await check_chat_needs_summary(contact.peer_id, owner.id)
                if summary_info:
                    await notification_queue.enqueue(
                        topic="chat_summary",
                        text=(
                            f"📊 В чате <b>{summary_info['chat_name']}</b> "
                            f"{summary_info['new_count']} новых сообщений. "
                            f"Сделать краткий пересказ?"
                        ),
                        priority=1,
                        category="chat_summary",
                        metadata={
                            "chat_id": contact.peer_id,
                            "action": "offer_summary",
                        },
                    )
            except Exception:
                logger.warning(
                    "summary check skip for %s", contact.display_name, exc_info=True
                )

    except Exception:
        logger.exception("proactive_chat_analyzer: scan failed")


async def _analyze_contact(
    contact,
    msg_count: int,
    client,
    provider,
    owner,
    telegram_id: int,
) -> None:
    """Анализировать один контакт: загрузить чат, сделать саммари, отправить уведомление."""
    from src.core.contacts.chat_service import load_chat
    from src.core.intelligence.summarizer import summarize_chat as _summarize

    messages = await load_chat(
        client, telegram_id, contact.peer_id, limit=50, transcribe=False
    )
    if not messages:
        return

    summary = await _summarize(
        provider, contact, messages, owner_id=owner.id, heavy=False
    )
    text = (
        f"📊 <b>Проактивный анализ: {contact.display_name}</b>\n"
        f"({msg_count} новых сообщ. за 24ч)\n\n{summary}"
    )
    await notification_queue.enqueue(
        topic="proactive-chat-analysis",
        text=text,
        priority=1,
        category="chat_analysis",
    )


async def _proactive_loop(telegram_id: int) -> None:
    """Бесконечный цикл с интервалом."""
    while True:
        if _overlap_guard.locked():
            await asyncio.sleep(INTERVAL_HOURS * 3600)
            continue
        async with _overlap_guard:
            try:
                await _proactive_scan(telegram_id)
            except Exception:
                logger.exception("proactive_analyzer iteration failed")
        await asyncio.sleep(INTERVAL_HOURS * 3600)


task_manager.register(
    "proactive-chat-analyzer",
    partial(_proactive_loop, settings.owner_telegram_id),
    restart_on_failure=True,
    restart_delay=120,
)
