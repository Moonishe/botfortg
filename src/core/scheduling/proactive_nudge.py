"""Proactive Nudge — reminds owner about unanswered important messages."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC
from functools import partial

from sqlalchemy import select, desc

from src.config import settings
from src.db.models import Message
from src.db.repo import (
    get_contact,
    get_or_create_user,
    list_active_conversations,
    list_open_commitments,
)
from src.db.session import get_session
from src.core.infra.task_manager import task_manager
from src.core.scheduling.notification_queue import notification_queue

logger = logging.getLogger(__name__)

_overlap_guard = asyncio.Lock()

# Urgency keywords — recognised in incoming messages
_URGENCY_KEYWORDS: frozenset[str] = frozenset({"срочно", "жду", "когда", "ответь", "?"})


async def collect_nudges(owner_telegram_id: int, limit: int = 5) -> list[dict]:
    """Scan conversations and collect nudges — contacts you should reply to.

    Smart timing: analyzes when each contact is typically online (by hour of day
    from message history) and only includes them in nudges during their active hours.
    """
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        now = datetime.now(UTC).replace(tzinfo=None)  # naive, project style

        conversations = await list_active_conversations(session, owner, limit=50)
        nudges: list[dict] = []

        for conv in conversations:
            incoming = conv.last_incoming_at
            if not incoming:
                continue

            outgoing = conv.last_outgoing_at
            if outgoing and outgoing > incoming:
                continue  # you replied last

            hours_waiting = (now - incoming).total_seconds() / 3600
            if hours_waiting < 24:
                continue

            contact = await get_contact(session, owner, conv.peer_id)
            if not contact:
                continue

            # Smart timing: check if contact is likely online now
            # Analyze their message hours from DB
            from sqlalchemy import func as sa_func, extract

            hour_result = await session.execute(
                select(
                    sa_func.count().label("cnt"),
                )
                .where(
                    Message.user_id == owner.id,
                    Message.peer_id == conv.peer_id,
                    Message.is_outgoing.is_(False),
                )
                .group_by(extract("hour", Message.date))
                .order_by(desc("cnt"))
                .limit(5)
            )
            active_hours = {row[0] for row in hour_result}
            current_hour = now.hour
            # If we have data and contact is NOT in their active hours → skip
            if active_hours and current_hour not in active_hours:
                continue

            commitments = await list_open_commitments(
                session, owner, peer_id=conv.peer_id
            )

            # Last incoming message text
            msg_r = await session.execute(
                select(Message.text)
                .where(
                    Message.user_id == owner.id,
                    Message.peer_id == conv.peer_id,
                    Message.is_outgoing.is_(False),
                )
                .order_by(desc(Message.date))
                .limit(1)
            )
            last_msg = msg_r.scalar_one_or_none()

            # Urgency scoring
            urgency = hours_waiting / 24  # base: days waiting
            if commitments:
                urgency += 2.0
            if last_msg and any(
                w in (last_msg or "").lower() for w in _URGENCY_KEYWORDS
            ):
                urgency += 1.5

            nudges.append(
                {
                    "peer_id": conv.peer_id,
                    "display_name": contact.display_name or str(conv.peer_id),
                    "hours_waiting": int(hours_waiting),
                    "last_msg": last_msg[:80] if last_msg else "",
                    "has_commitments": len(commitments),
                    "urgency": urgency,
                }
            )

        nudges.sort(key=lambda x: x["urgency"], reverse=True)
        return nudges[:limit]


def format_nudge(nudges: list[dict]) -> str:
    """Format nudges as humanized notification text."""
    if not nudges:
        return ""

    lines = ["🔔 <b>Напомню</b>\n"]
    for n in nudges[:3]:
        name = n["display_name"]
        hours = n["hours_waiting"]
        if hours < 48:
            when = f"{hours}ч"
        elif hours < 168:
            when = f"{hours // 24}дн"
        else:
            when = f"{hours // 24}дн"

        lines.append(f"• <b>{name}</b> — ждёт уже {when}")
        if n["last_msg"]:
            lines.append(f"  <i>«{n['last_msg'][:60]}»</i>")
        if n["has_commitments"]:
            lines.append(f"  📋 открыто обещаний: {n['has_commitments']}")

    result = "\n".join(lines)
    try:
        from src.core.humanizer.humanizer import humanize_response

        result = await asyncio.to_thread(humanize_response, result) or result
    except Exception:
        logger.debug("Non-critical error", exc_info=True)
    return result


async def nudge_loop(owner_telegram_id: int) -> None:
    """Background loop: check every 3 hours for unanswered messages."""
    while True:
        if _overlap_guard.locked():
            await asyncio.sleep(10800)
            continue
        async with _overlap_guard:
            try:
                nudges = await collect_nudges(owner_telegram_id)
                if nudges:
                    text = format_nudge(nudges)
                    if text:
                        from src.db.models import Notification

                        await notification_queue.enqueue(
                            topic="nudge",
                            text=text,
                            priority=Notification.PRIORITY_HIGH,
                        )
                        logger.info("Nudge sent: %d contacts", len(nudges))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Nudge loop error")
        await asyncio.sleep(10800)  # 3 hours


# Register via task_manager — matches project pattern (proactive_briefing.py)
task_manager.register(
    "proactive-nudge",
    partial(nudge_loop, settings.owner_telegram_id),
    restart_on_failure=True,
    restart_delay=60,
)
