"""Auto-reply context utilities: offline detection, memory gathering,
profile hints, system prompt assembly. Extracted from auto_reply.py
for maintainability — context-building logic separated from handler."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, UTC

from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    UserStatusLastMonth,
    UserStatusLastWeek,
    UserStatusOffline,
    UserStatusOnline,
    UserStatusRecently,
)

from src.core.infra.timeutil import get_user_tz, now_in_tz
from src.core.memory.memory_recall import format_recall_for_prompt, recall
from src.db.models import User
from src.db.repo import get_contact_profile
from src.db.session import get_session

logger = logging.getLogger(__name__)


async def _check_and_track_offline(
    client: TelegramClient, session: AsyncSession, owner: User
) -> bool:
    try:
        me = await client.get_me()
        status = getattr(me, "status", None)
        if isinstance(status, UserStatusOnline):
            owner.last_seen_online = datetime.now(UTC).replace(tzinfo=None)
            # Сброс absence статуса — владелец онлайн
            if owner.absence_status in ("sleeping", "away", "soon_back"):
                owner.absence_status = None
                owner.absence_message = None
            await session.flush()
            return False
        if isinstance(
            status, (UserStatusRecently, UserStatusLastWeek, UserStatusLastMonth)
        ):
            # Owner was recently online — not definitely offline, do not auto-reply
            owner.last_seen_online = datetime.now(UTC).replace(tzinfo=None)
            await session.flush()
            return False
        if isinstance(status, UserStatusOffline):
            now = datetime.now(UTC).replace(tzinfo=None)
            last_seen = owner.last_seen_online
            if last_seen is None or (now - last_seen) > timedelta(minutes=10):
                # Sleep detection — configurable window in user's timezone
                tz_name = get_user_tz(owner)
                local_now = now_in_tz(tz_name)
                hour = local_now.hour
                # ponytail: configurable hours, default 23-07. Upgrade to adaptive if patterns tracked.
                sleep_start = (
                    getattr(owner.settings, "sleep_start_hour", 23)
                    if owner.settings
                    else 23
                )
                sleep_end = (
                    getattr(owner.settings, "sleep_end_hour", 7)
                    if owner.settings
                    else 7
                )
                if sleep_start > sleep_end:
                    is_night = hour >= sleep_start or hour < sleep_end
                else:
                    is_night = sleep_start <= hour < sleep_end

                if is_night:
                    if last_seen is not None:
                        offline_minutes = (now - last_seen).total_seconds() / 60
                        if offline_minutes > 30 and owner.absence_status != "sleeping":
                            owner.absence_status = "sleeping"
                            owner.absence_message = (
                                f"Спит с {local_now.strftime('%H:%M')}"
                            )
                            await session.flush()
                else:
                    # Дневное время — сброс sleeping статуса
                    if owner.absence_status == "sleeping":
                        owner.absence_status = None
                        owner.absence_message = None
                        await session.flush()

                return True
            return False
        return True
    except FloodWaitError as e:
        # ponytail: cap FloodWait at 60s — was: await asyncio.sleep(e.seconds)
        # which could block the handler for 86400+ seconds (Telegram can demand
        # hours-long waits). 60s cap keeps handler responsive; Telegram will
        # re-issue FloodWait if still rate-limited.
        _FLOOD_WAIT_CAP = 60
        wait = max(0.0, min(e.seconds, _FLOOD_WAIT_CAP))
        logger.warning(
            "FloodWait %ds (capped to %ds) in _check_and_track_offline — retrying after delay",
            e.seconds,
            wait,
        )
        await asyncio.sleep(wait)
        try:
            me = await client.get_me()
            return me is not None
        except Exception:
            logger.warning("get_me still failing after capped FloodWait — skipping")
            return False
    except Exception:
        logger.exception("get_me failed in _check_and_track_offline")
        return False


async def _gather_memory_context(
    owner_telegram_id: int,
    peer_id: int,
    incoming_text: str,
) -> str:
    """Gather memory context via digest (fast precomputed) or full recall (fallback).

    Returns formatted memory_context string for system prompt injection.
    """
    memory_context = ""
    digest_used = False
    try:
        from src.core.contacts.contact_memory_digest import (
            get_contact_digest,
        )

        digest = await get_contact_digest(owner_telegram_id, peer_id)
        if digest.get("facts"):
            # Use digest facts — much faster than full recall
            memory_context = (
                "<recall_context>\n"
                + "\n".join(f"- {f['fact']}" for f in digest["facts"][:5])
                + "\n</recall_context>"
            )
            digest_used = True
        else:
            raise ValueError("empty digest facts")
    except Exception:
        logger.debug(
            "Digest unavailable for peer %d, falling back to recall",
            peer_id,
        )

    # Fallback: full recall if digest didn't provide facts
    if not digest_used:
        try:
            result = await recall(
                owner_telegram_id,
                contact_id=peer_id,
                query=incoming_text[:200],
                limit=8,
                include_self=True,
                include_pinned=True,
                include_tasks=False,
                mode="light",
            )
            # ponytail: single recall — light mode is sufficient; if <3 facts,
            # memory_context is still useful (was: retry with normal mode = 2x cost)
            memory_context = format_recall_for_prompt(result)
        except Exception:
            logger.warning("recall failed, skipping memory context")

    # C2: Hierarchical context — add conversation summary for long chat history
    try:
        from sqlalchemy import select as sa_select
        from sqlalchemy import desc as sa_desc
        from src.db.models._messaging import ConversationSummary

        async with get_session() as _session:
            _sum_row = await _session.execute(
                sa_select(ConversationSummary)
                .where(
                    ConversationSummary.user_id == owner_telegram_id,
                    ConversationSummary.last_peer_id == peer_id,
                )
                .order_by(sa_desc(ConversationSummary.created_at))
                .limit(1)
            )
            _prev_sum = _sum_row.scalar_one_or_none()
        if _prev_sum and _prev_sum.summary_text:
            memory_context += (
                f"\n\n<chat_summary>\n{_prev_sum.summary_text}\n</chat_summary>"
            )
    except Exception:
        logger.debug(
            "Failed to load chat summary for hierarchical context", exc_info=True
        )

    return memory_context


async def _gather_profile_hints(
    session: AsyncSession,
    owner: User,
    peer_id: int,
) -> str:
    """Gather contact profile hints (communication style, dos/donts).

    Returns formatted profile_prompt string or empty string.
    """
    profile_prompt = ""
    try:
        profile = await get_contact_profile(session, owner, peer_id)
        if profile:
            profile_hints = []
            if profile.communication_style:
                profile_hints.append(f"Стиль общения: {profile.communication_style}")
            if profile.communication_dos:
                dos_list = (
                    json.loads(profile.communication_dos)
                    if isinstance(profile.communication_dos, str)
                    and (profile.communication_dos or "").startswith("[")
                    else [profile.communication_dos]
                )
                profile_hints.append(f"МОЖНО: {', '.join(dos_list[:4])}")
            if profile.communication_donts:
                donts_list = (
                    json.loads(profile.communication_donts)
                    if isinstance(profile.communication_donts, str)
                    and (profile.communication_donts or "").startswith("[")
                    else [profile.communication_donts]
                )
                profile_hints.append(f"НЕЛЬЗЯ: {', '.join(donts_list[:4])}")
            if profile_hints:
                profile_prompt = "\n\nПРОФИЛЬ КОНТАКТА:\n" + "\n".join(profile_hints)
    except Exception:
        logger.debug("get_contact_profile failed, skipping profile hints")

    return profile_prompt


async def _build_system_prompt(
    base: str,
    *,
    memory_context: str = "",
    profile_prompt: str = "",
    style_hint: str = "",
    contact_archetype: str | None = None,
    owner_absence_status: str | None = None,
    owner_absence_message: str | None = None,
    owner_telegram_id: int = 0,
    peer_id: int = 0,
) -> str:
    """Assemble the full system prompt from all context parts.

    Accepts AUTO_REPLY_SYSTEM_BASE as ``base`` to avoid circular imports
    between auto_reply_context and auto_reply (facade).
    """
    system = base
    if memory_context:
        system = system + "\n\n" + memory_context
    if owner_absence_status == "away":
        system += f"\n\nВАЖНО: Владелец сказал перед уходом: «{owner_absence_message}». Учти это в ответе. Он отсутствует."
    elif owner_absence_status == "soon_back":
        system += f"\n\nВладелец скоро вернётся: «{owner_absence_message}». Ответь обнадёживающе, он скоро будет."
    elif owner_absence_status == "sleeping":
        system += (
            f"\n\n🌙💤 Владелец СПИТ ({owner_absence_message}). "
            "Никаких «занят» или «не у телефона» — честно скажи что он спит. "
            "Используй эмодзи: 😴🛏️🌙💤🌌. Тон: заботливый, сонный. "
            "Пример: «Владелец сейчас спит сладким сном 😴💤 "
            "Как проснётся — обязательно ответит! 🌙»"
        )
    if style_hint:
        system = system + "\n" + style_hint

    # Архетип контакта (подсказка для тона)
    if contact_archetype:
        from src.core.contacts.contact_archetypes import archetype_reply_hint

        hint = archetype_reply_hint(contact_archetype)
        if hint:
            system += hint

    if profile_prompt:
        system += profile_prompt

    # Per-contact rules (custom_instructions)
    try:
        from src.core.contacts.contact_rules import get_contact_rules_block

        _rules_block = await get_contact_rules_block(owner_telegram_id, peer_id)
        if _rules_block:
            system += "\n\n" + _rules_block
    except Exception:
        logger.debug("Failed to load contact rules block in auto-reply", exc_info=True)

    return system
