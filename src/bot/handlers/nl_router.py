"""NL Router — понимает естественный язык и роутит на команды.

Ponytail: pattern matching for top 30 intents (covers 90% of usage).
For everything else, the LLM free_text pipeline handles it — the LLM
already knows about commands via prompt_assembler.py.

Usage:
    from src.bot.handlers.nl_router import try_nl_route
    result = await try_nl_route(text, message, state, userbot_manager)
    if result: return  # handled, don't proceed to LLM
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram.fsm.context import FSMContext
    from aiogram.types import InlineKeyboardButton, Message
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from src.userbot.manager import UserbotManager
    from src.core.infra.full_analyzer import AnalysisResult

logger = logging.getLogger(__name__)

# ── Pattern → command mapping ──────────────────────────────────────
# Each pattern: (regex, command, args_extractor)
# args_extractor: function(match) → str of args for the command

_NL_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # ── Memory ──
    (re.compile(r"покажи память (про|о|для)\s+(.+)", re.I), "memory", r"\2"),
    (
        re.compile(r"что (я|ты) (знаю|помнит|помнит) (о|про)\s+(.+)", re.I),
        "memory",
        r"\4",
    ),
    (re.compile(r"факты (о|про)\s+(.+)", re.I), "memory", r"\2"),
    (re.compile(r"помнишь\s+(.+)", re.I), "memory", r"\1"),
    (re.compile(r"запомни\s+(.+)", re.I), "remember", r"\1"),
    (re.compile(r"забудь\s+(.+)", re.I), "forget", r"\1"),
    (re.compile(r"удали (факт|память)\s+(.+)", re.I), "forget", r"\2"),
    (re.compile(r"экспорт.*памят", re.I), "mem_export", ""),
    (re.compile(r"дубликат.*факт", re.I), "mem_dedup", ""),
    (re.compile(r"похож.*факт.*на\s+(.+)", re.I), "mem_similar", r"\1"),
    (re.compile(r"(теплова|уверенност).*(памят|факт)", re.I), "mem_heatmap", ""),
    (re.compile(r"истека.*факт", re.I), "mem_expire", ""),
    (re.compile(r"теги?( памяти)?", re.I), "mem_tags", ""),
    # ── Planning ── (BEFORE chat — "напомни" must not be caught by "напиши")
    (re.compile(r"напомни\s+(.+)", re.I), "nlcron", r"\1"),
    (re.compile(r"напомнить\s+(.+)", re.I), "nlcron", r"\1"),
    (re.compile(r"не забудь\s+(.+)", re.I), "nlcron", r"\1"),
    (re.compile(r"день рожден", re.I), "birthdays", ""),
    (re.compile(r"дни рожден", re.I), "birthdays", ""),
    (re.compile(r"покажи расписани", re.I), "calendar", ""),
    (
        re.compile(r"(покажи |мои |есть )?\bзадачи?\b( на| на сегодня)?", re.I),
        "cron",
        "",
    ),
    (re.compile(r"намерен(ие|ия)? (на|на сегодня)", re.I), "intention", ""),
    (re.compile(r"недельн.*отч", re.I), "weekly", ""),
    # ── Chat ── (after planning — "напомни" won't be caught by "напиши")
    (re.compile(r"напиши\s+(.+)", re.I), "chat", r"\1"),
    (re.compile(r"ответь\s+(.+)", re.I), "chat", r"\1"),
    (re.compile(r"синхронизируй (контакт|диалог|чат)", re.I), "sync", ""),
    (re.compile(r"обнови контакт", re.I), "sync", ""),
    (re.compile(r"входящ(ие|ие сообщения)", re.I), "inbox", ""),
    (re.compile(r"что (я )?пропустил", re.I), "inbox", ""),
    (re.compile(r"последние сообщения\s*(от\s+)?(.+)", re.I), "recent", r"\2"),
    # ── Search ──
    (re.compile(r"найди\s+(.+)", re.I), "search", r"\1"),
    (re.compile(r"поиск\s+(по|в)?\s*(.+)", re.I), "search", r"\2"),
    # ── Settings ──
    (re.compile(r"настройки", re.I), "settings", ""),
    (re.compile(r"настрой\s+(.+)", re.I), "settings", ""),
    (re.compile(r"включи\s+(.+)", re.I), "settings", r"\1"),
    (re.compile(r"выключи\s+(.+)", re.I), "settings", r"\1"),
    (re.compile(r"(api|апи)?\s*ключ", re.I), "keys", ""),
    (re.compile(r"модел", re.I), "models", ""),
    # ── Analytics ──
    (re.compile(r"статистик", re.I), "stats", ""),
    (re.compile(r"сколько (фактов|контактов|сообщений)", re.I), "stats", ""),
    (re.compile(r"здоровь", re.I), "health", ""),
    (re.compile(r"статус (систем|бот)", re.I), "health", ""),
    (re.compile(r"рост памяти", re.I), "memory_growth", ""),
    (re.compile(r"журнал.*снов", re.I), "dreams", ""),
    # ── Tools ──
    (re.compile(r"переведи\s+(.+)", re.I), "translate", r"\1"),
    (re.compile(r"перевод\s+(.+)", re.I), "translate", r"\1"),
    (re.compile(r"валют|курс", re.I), "currency", ""),
    (re.compile(r"погод", re.I), "weather_clothing", ""),
    (re.compile(r"суммируй\s+(.+)", re.I), "url_summary", r"\1"),
    (re.compile(r"перескажи\s+(.+)", re.I), "url_summary", r"\1"),
    (re.compile(r"выполни код\s*(.+)?", re.I), "code", r"\1"),
    (re.compile(r"анализ.*сообщен", re.I), "analyze", ""),
    # ── Auto-reply ──
    (re.compile(r"я (уехал|отошёл|отошел|занят)", re.I), "away", "away"),
    (re.compile(r"я сплю", re.I), "away", "sleeping"),
    (re.compile(r"я вернулся|я тут", re.I), "away", "off"),
    # ── Intelligence ──
    (re.compile(r"граф.*знани", re.I), "graph", ""),
    (re.compile(r"(покажи |мои )сущност(и|ей|ь)", re.I), "entities", ""),
    (re.compile(r"(покажи |уровень )?уверенност", re.I), "confidence", ""),
]


def _match_nl(text: str) -> tuple[str, str] | None:
    """Try to match text against NL patterns.

    Returns (command, args) or None.
    """
    text = text.strip()
    if not text or text.startswith("/"):
        return None  # already a command

    for pattern, command, args_template in _NL_PATTERNS:
        m = pattern.search(text)
        if m:
            # Extract args from match groups
            if args_template and any(f"\\{i}" in args_template for i in range(1, 10)):
                args = args_template
                groups = m.groups()
                for i in range(1, len(groups) + 1):
                    args = args.replace(f"\\{i}", groups[i - 1] or "")
                args = args.strip()
            elif args_template:
                args = args_template.strip()
            else:
                args = ""
            logger.debug("NL route: '%s' → /%s %s", text[:50], command, args)
            return command, args.strip()
    return None


async def try_nl_route(
    text: str,
    message: "Message",
    state: "FSMContext | None" = None,
    userbot_manager: "UserbotManager | None" = None,
) -> bool:
    """Try to route NL text to a command.

    Returns True if handled (caller should return early).
    Returns False if no match (caller should proceed to LLM pipeline).
    """
    match = _match_nl(text)
    if match is None:
        return False

    command, args = match
    # Simulate command by sending it as a message to the chat
    # aiogram will pick it up because the bot processes owner messages
    cmd_text = f"/{command}"
    if args:
        cmd_text += f" {args}"

    # Send confirmation + command to the chat
    # The bot will process the command on the next message cycle
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"✅ /{command} {args}".strip(),
        callback_data=f"nlrun:{command}:{args[:50]}",
    )
    await message.answer(
        f"🔍 Понял: <code>/{command} {args}</code>\nНажми чтобы выполнить:",
        reply_markup=kb.as_markup(),
    )
    return True


# ── Briefing inline buttons ────────────────────────────────────────


def briefing_keyboard(
    waiting_contacts: list[tuple[int, str, str]],
) -> "InlineKeyboardBuilder | None":
    """Build inline keyboard for morning briefing with action buttons.

    Args:
        waiting_contacts: list of (peer_id, name, snippet)

    Returns:
        InlineKeyboardBuilder with [Ответить] [Отложить] [Игнорировать] per contact,
        or None if no contacts.
    """
    if not waiting_contacts:
        return None

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    for peer_id, name, _snippet in waiting_contacts[
        :5
    ]:  # top 5 to avoid keyboard bloat
        short_name = name[:20] if name else str(peer_id)
        kb.row(
            InlineKeyboardButton(
                text=f"💬 {short_name}",
                callback_data=f"briefing:reply:{peer_id}",
            ),
            InlineKeyboardButton(
                text="⏰ Отложить",
                callback_data=f"briefing:snooze:{peer_id}",
            ),
            InlineKeyboardButton(
                text="✖",
                callback_data=f"briefing:ignore:{peer_id}",
            ),
        )
    return kb


# ── Settings inline menu ───────────────────────────────────────────


async def settings_inline_menu() -> "InlineKeyboardBuilder":
    """Build inline settings menu with all toggles.

    Shows current state of each toggle — tap to switch.
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from src.config import settings as cfg
    from src.db.session import get_session
    from src.db.repo import get_or_create_user

    # Get user settings from DB
    toggles: list[tuple[str, str, bool]] = []
    try:
        # Read global toggles from config
        toggles.append(("streaming", "Стриминг ответов", cfg.streaming_enabled))
        toggles.append(
            ("pacing", "Естественная задержка", cfg.response_pacing_mode != "off")
        )
        toggles.append(("group_enabled", "Ответы в группах", cfg.userbot_group_enabled))
        toggles.append(("rate_limit", "Rate limit", cfg.rate_limit_per_min > 0))
    except Exception:
        logger.debug("Failed to read config toggles", exc_info=True)

    kb = InlineKeyboardBuilder()
    for toggle_id, label, is_on in toggles:
        icon = "🟢" if is_on else "⚪"
        kb.row(
            InlineKeyboardButton(
                text=f"{icon} {label}",
                callback_data=f"set:toggle:{toggle_id}",
            )
        )
    kb.row(
        InlineKeyboardButton(text="🔄 Память", callback_data="set:cat:memory"),
        InlineKeyboardButton(text="🔑 Ключи", callback_data="set:cat:keys"),
    )
    kb.row(
        InlineKeyboardButton(text="🤖 Авто-ответы", callback_data="set:cat:autoreply"),
        InlineKeyboardButton(text="📋 Cron", callback_data="set:cat:cron"),
    )
    kb.row(
        InlineKeyboardButton(text="✖ Закрыть", callback_data="set:close"),
    )
    return kb


# ── Undo button ────────────────────────────────────────────────────


def undo_keyboard(action_type: str, action_id: int | str) -> "InlineKeyboardBuilder":
    """Build undo keyboard for bot actions.

    Args:
        action_type: "memory" | "autoreply" | "commitment"
        action_id: ID of the action to undo
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(text="↩ Отменить", callback_data=f"undo:{action_type}:{action_id}")
    return kb


# ── Proactive insights ─────────────────────────────────────────────


async def generate_insights(owner_id: int) -> list[str]:
    """Generate actionable insights from analysis results.

    Instead of "4 факта, 5 обязательств", produces:
    - "Илья ждал ответа 3 дня — написать?"
    - "Ты обещал Максу позвонить — напомнить?"
    - "У Дениса день рождения через 2 дня"
    """
    insights: list[str] = []

    # Stale contacts (waiting reply > 2 days)
    from datetime import datetime, timedelta, UTC
    from src.db.session import get_session
    from src.db.repo import get_or_create_user, list_contacts
    from sqlalchemy import select, func
    from src.db.models import Message

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, owner_id)
            # This is called after analysis, so we check for stale contacts
            contacts = await list_contacts(
                session, owner, kinds=("user",), include_bots=False
            )
            now = datetime.now(UTC).replace(tzinfo=None)
            cutoff = now - timedelta(days=2)

            for contact in contacts[:10]:
                # Last incoming message date
                last_in = await session.scalar(
                    select(func.max(Message.date)).where(
                        Message.user_id == owner.id,
                        Message.peer_id == contact.peer_id,
                        Message.is_outgoing.is_(False),
                    )
                )
                last_out = await session.scalar(
                    select(func.max(Message.date)).where(
                        Message.user_id == owner.id,
                        Message.peer_id == contact.peer_id,
                        Message.is_outgoing.is_(True),
                    )
                )

                if last_in and (last_out is None or last_out < last_in):
                    days_waiting = (now - last_in).days
                    if days_waiting >= 2:
                        name = contact.display_name or str(contact.peer_id)
                        insights.append(
                            f"⏰ {name} ждёт ответа {days_waiting} дн. — написать?"
                        )

            # Check commitments
            from src.db.models import Commitment
            from src.db.repo import list_open_commitments

            mine = await list_open_commitments(session, owner, direction="mine")
            for c in mine[:5]:
                if c.deadline_at:
                    days_left = (c.deadline_at - now).days
                    if 0 <= days_left <= 3:
                        name = c.peer_name or str(c.peer_id)
                        insights.append(
                            f"📝 Ты обещал {name}: {c.text[:40]} — "
                            f"{'сегодня' if days_left == 0 else f'через {days_left} дн.'}"
                        )

    except Exception:
        logger.debug("Insight generation failed", exc_info=True)

    return insights[:5]  # top 5 actionable
