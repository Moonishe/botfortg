import asyncio
import logging
from datetime import datetime, timedelta, UTC

from src.core.infra.text_sanitizer import sanitize_html
from src.core.scheduling.notification_queue import notification_queue
from src.db.models import Notification
from src.db.repo import (
    get_or_create_user,
    get_contacts_by_peer_ids,
    list_active_conversations,
)
from functools import partial

from src.config import settings
from src.core.infra.timeutil import ensure_utc
from src.db.session import get_session
from src.core.infra.task_manager import task_manager

logger = logging.getLogger(__name__)

# ── Защита от наложения (overlap guard) ──
# Если предыдущий тик follow_up_loop ещё не завершился — пропускаем,
# чтобы избежать дублирования нотификаций о непрочитанных сообщениях.
_overlap_guard = asyncio.Lock()


async def follow_up_loop(owner_id: int) -> None:
    """Проверка переписок без ответа >24 часов, раз в 4 часа."""
    try:
        while True:
            if _overlap_guard.locked():
                await asyncio.sleep(settings.follow_up_interval_sec)
                continue

            async with _overlap_guard:
                try:
                    async with get_session() as session:
                        owner = await get_or_create_user(session, owner_id)
                        cutoff = datetime.now(UTC) - timedelta(hours=24)
                        convos = await list_active_conversations(
                            session, owner, status="waiting_reply", limit=30
                        )
                        peer_ids = {conv.peer_id for conv in convos}
                        contacts = await get_contacts_by_peer_ids(
                            session, owner, peer_ids
                        )
                        stale: list[str] = []
                        for conv in convos:
                            last_in = ensure_utc(conv.last_incoming_at)
                            last_out = ensure_utc(conv.last_outgoing_at)
                            if last_in and last_in < cutoff:
                                if last_out is None or last_out < last_in:
                                    contact = contacts.get(conv.peer_id)
                                    raw_name = (
                                        contact.display_name
                                        if contact and contact.display_name
                                        else str(conv.peer_id)
                                    )
                                    name = sanitize_html(raw_name)
                                    stale.append(name)

                        if stale:
                            names = ", ".join(stale[:5])
                            suffix = (
                                f" и ещё {len(stale) - 5}" if len(stale) > 5 else ""
                            )
                            await notification_queue.enqueue(
                                topic="follow_up",
                                text=f"⚠️ <b>Без ответа >24ч:</b> {names}{suffix}\n"
                                f"<i>/threads — просмотреть и ответить</i>",
                                priority=Notification.PRIORITY_HIGH,
                            )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("FollowUp loop error")

            await asyncio.sleep(settings.follow_up_interval_sec)
    except asyncio.CancelledError:
        logger.info("follow_up_loop: cancelled")
        raise


task_manager.register("follow-up", partial(follow_up_loop, settings.owner_telegram_id))
