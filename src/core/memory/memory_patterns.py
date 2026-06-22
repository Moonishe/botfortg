"""Proactive pattern detection — находит закономерности в памяти и предлагает действия."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, UTC
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram.types import InlineKeyboardMarkup

# ADR-001: Core builds InlineKeyboardMarkup via lazy import inside functions.
# See src/core/services/chat_actions.py for full rationale.

from src.core.scheduling.notification_queue import notification_queue
from src.db.models import Notification
from src.core.infra.timeutil import get_user_tz, now_in_tz
from src.db.repo import (
    get_or_create_user,
    list_contacts,
    list_memories,
)
from src.config import settings
from src.db.session import get_session

logger = logging.getLogger(__name__)

_overlap_guard = asyncio.Lock()


async def detect_patterns(owner_id: int) -> list[dict]:
    """
    Анализирует память и возвращает список инсайтов.
    Каждый инсайт: {"type": str, "title": str, "detail": str, "action": str}
    Типы: "periodic_contact", "stale_negative", "sentiment_shift", "unfinished_topic"
    """
    insights: list[dict] = []
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        memories = await list_memories(session, owner)
        contacts_list = await list_contacts(
            session, owner, kinds=("user",), include_bots=False
        )

        # P5: Pre-load contacts into dict to avoid N+1 get_contact() calls.
        # Keyed by peer_id because Memory.contact_id stores Telegram peer_id (BigInteger),
        # NOT the Contact's auto-increment PK (c.id).
        _contacts_cache: dict[int, Any] = {c.peer_id: c for c in contacts_list}

        def _contact_name(contact_id: int) -> str:
            c = _contacts_cache.get(contact_id)
            return getattr(c, "display_name", None) or str(contact_id)

        # ---- Инсайт 1: периодические контакты ----
        contact_activity: dict[int, list[datetime]] = defaultdict(list)
        for m in memories:
            if m.contact_id is not None and m.created_at is not None:
                contact_activity[m.contact_id].append(m.created_at)

        for contact_id, dates in contact_activity.items():
            if len(dates) >= 3:
                wdays: dict[int, int] = defaultdict(int)
                for d in dates:
                    wdays[d.weekday()] += 1
                best_day = max(wdays, key=wdays.get)  # type: ignore[arg-type]
                if wdays[best_day] >= 3:  # 3+ совпадений дня недели
                    name = _contact_name(contact_id)
                    day_names = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
                    insights.append(
                        {
                            "type": "periodic_contact",
                            "contact_id": contact_id,
                            "title": f"📅 Регулярный контакт: {name}",
                            "detail": f"Ты общаешься с {name} каждую {day_names[best_day]} ({wdays[best_day]} раз за период).",
                            "action": f"Поставить еженедельное напоминание на {day_names[best_day]}?",
                        }
                    )

        # ---- Инсайт 2: забытые негативные контакты ----
        now = datetime.now(UTC)
        contact_last_neg: dict[int, tuple[datetime, str]] = {}
        for m in memories:
            if (
                m.sentiment in ("negative", "contradictory")
                and m.contact_id
                and m.created_at
            ):
                if (
                    m.contact_id not in contact_last_neg
                    or m.created_at > contact_last_neg[m.contact_id][0]
                ):
                    contact_last_neg[m.contact_id] = (m.created_at, m.fact)

        for contact_id, (last_date, fact) in contact_last_neg.items():
            days_since = (now - last_date).days
            if days_since > 14:
                name = _contact_name(contact_id)
                insights.append(
                    {
                        "type": "stale_negative",
                        "contact_id": contact_id,
                        "title": f"⚠️ Давно без контакта: {name}",
                        "detail": f"Последний негативный факт {days_since} дн. назад: «{fact[:80]}». Может написать?",
                        "action": f"Открыть /threads и проверить переписку с {name}",
                    }
                )

        # ---- Инсайт 3: сдвиг настроения ----
        contact_sentiments: dict[int, list[tuple[datetime, str]]] = defaultdict(list)
        for m in memories:
            if m.contact_id and m.sentiment and m.created_at:
                contact_sentiments[m.contact_id].append((m.created_at, m.sentiment))

        for contact_id, sent_list in contact_sentiments.items():
            if len(sent_list) >= 5:
                sorted_list = sorted(sent_list, key=lambda x: x[0])
                mid = len(sorted_list) // 2
                old = [s[1] for s in sorted_list[:mid]]
                new = [s[1] for s in sorted_list[mid:]]
                old_neg = sum(
                    1 for s in old if s in ("negative", "contradictory")
                ) / len(old)
                new_neg = sum(
                    1 for s in new if s in ("negative", "contradictory")
                ) / len(new)
                if new_neg - old_neg > 0.3:  # ухудшение
                    name = _contact_name(contact_id)
                    insights.append(
                        {
                            "type": "sentiment_shift",
                            "contact_id": contact_id,
                            "title": f"📉 Ухудшение отношений: {name}",
                            "detail": f"Негатив вырос с {int(old_neg * 100)}% до {int(new_neg * 100)}%. Проверь что происходит.",
                            "action": f"Написать {name} или /chat {name}",
                        }
                    )
                elif old_neg - new_neg > 0.3:  # улучшение
                    name = _contact_name(contact_id)
                    insights.append(
                        {
                            "type": "sentiment_shift",
                            "contact_id": contact_id,
                            "title": f"📈 Улучшение отношений: {name}",
                            "detail": f"Негатив снизился с {int(old_neg * 100)}% до {int(new_neg * 100)}%. Отлично!",
                            "action": f"Закрепить успех — написать {name}",
                        }
                    )

    return insights


def insights_keyboard(insight: dict) -> "InlineKeyboardMarkup | None":
    """Возвращает inline-клавиатуру для инсайта по его типу."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    t = insight["type"]
    contact_id = insight.get("contact_id", 0)

    if t == "periodic_contact":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📅 Поставить напоминание",
                        callback_data=f"pattern:remind:{contact_id}",
                    ),
                    InlineKeyboardButton(
                        text="🔕 Не сейчас", callback_data="pattern:dismiss"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="📊 История контакта",
                        callback_data=f"pattern:history:{contact_id}",
                    ),
                ],
            ]
        )
    if t == "stale_negative":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💬 Написать",
                        callback_data=f"pattern:write:{contact_id}",
                    ),
                    InlineKeyboardButton(
                        text="🔕 Не сейчас", callback_data="pattern:dismiss"
                    ),
                ],
            ]
        )
    if t == "sentiment_shift":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💬 Написать",
                        callback_data=f"pattern:write:{contact_id}",
                    ),
                    InlineKeyboardButton(
                        text="📊 Анализ",
                        callback_data=f"pattern:history:{contact_id}",
                    ),
                ],
            ]
        )
    return None


def format_insights(
    insights: list[dict],
) -> tuple[str, list["InlineKeyboardMarkup | None"]]:
    """Форматирует инсайты в HTML для отправки.

    Возвращает (текст, список клавиатур) — клавиатура для каждого инсайта.
    """
    if not insights:
        return (
            "🧠 Анализ паттернов: всё стабильно. Необычных паттернов не обнаружено.",
            [None],
        )
    lines: list[str] = ["<b>🧠 Инсайты из памяти:</b>", ""]
    keyboards: list[InlineKeyboardMarkup | None] = []
    for i, ins in enumerate(insights[:5]):
        lines.append(f"{i + 1}. {ins['title']}")
        lines.append(f"   {ins['detail']}")
        lines.append(f"   💡 {ins['action']}")
        lines.append("")
        keyboards.append(insights_keyboard(ins))
    return "\n".join(lines), keyboards


async def mint_l2_rules(insights: list[dict], owner_id: int) -> list[str]:
    """LLM-mint: превращает статистические инсайты в человеко-читаемые L2-правила.

    ponytail: one LLM call per patterns_loop run, upgrade to batched if patterns grow.
    """
    if not insights:
        return []

    from src.llm.provider_manager import build_provider
    from src.llm.base import ChatMessage

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, owner_id)
            provider = await build_provider(
                session, owner, purpose="background", task_type="summarize"
            )

        if provider is None:
            logger.debug("mint_l2_rules: no provider available")
            return []

        # Serialize insights for LLM
        insight_text = "\n".join(
            f"- {ins['title']}: {ins['detail']}" for ins in insights[:5]
        )

        from src.agents._json_utils import extract_json_from_llm_response
        from src.core.security.prompt_guard import scrub_internal_tags

        prompt = (
            "Ты — аналитик паттернов общения. Преврати эти статистические инсайты "
            "в 1-3 кратких человеко-читаемых правила поведения.\n\n"
            f"Инсайты:\n{insight_text}\n\n"
            'Верни JSON: {"rules": ["правило1", "правило2", ...]}\n'
            'Пример: {"rules": ["Пользователь общается с X каждую среду — '
            'предлагать напоминание", "Отношения с Y ухудшаются — быть мягче"]}'
        )

        raw = await provider.chat(
            [
                ChatMessage(role="system", content="Ты — аналитик паттернов."),
                ChatMessage(role="user", content=prompt),
            ],
            heavy=False,
            max_tokens=500,
        )
        text = scrub_internal_tags(raw)
        parsed = extract_json_from_llm_response(text, default={})
        rules = parsed.get("rules", []) if isinstance(parsed, dict) else []

        if not rules:
            logger.debug("mint_l2_rules: LLM returned no rules")
            return []

        # Save rules as memory facts for persistence
        from src.core.memory.memory_service import save_memory_single

        async with get_session() as session:
            owner = await get_or_create_user(session, owner_id)
            for rule in rules[:3]:
                await save_memory_single(
                    session,
                    owner,
                    fact=rule,
                    memory_type="l2_policy",
                    confidence=0.7,
                    source="auto",
                )

        logger.info("mint_l2_rules: generated %d rules", len(rules))
        return rules

    except Exception:
        logger.exception("mint_l2_rules: failed")
        return []


async def patterns_loop(owner_id: int) -> None:
    """Фоновый цикл: раз в 24 часа в 10:00 по часовому поясу владельца."""
    last_run_date: object = None
    while True:
        sleep_sec = settings.memory_patterns_interval_sec
        async with _overlap_guard:
            try:
                async with get_session() as session:
                    owner = await get_or_create_user(session, owner_id)
                    tz_name = get_user_tz(owner)

                now = now_in_tz(tz_name)
                today = now.date()
                if now.hour == 10 and last_run_date != today:
                    last_run_date = today
                    insights = await detect_patterns(owner_id)
                    # LLM-mint: generate human-readable L2 rules from statistical insights
                    if insights:
                        rules = await mint_l2_rules(insights, owner_id)
                        if rules:
                            insights.append(
                                {
                                    "type": "l2_policy",
                                    "title": "📋 Сформированы правила поведения",
                                    "detail": "\n".join(f"• {r}" for r in rules),
                                    "action": "Правила учтены в памяти",
                                }
                            )
                    text, keyboards = format_insights(insights)
                    if not insights:
                        await notification_queue.enqueue(
                            topic="memory_patterns",
                            text=text,
                            priority=Notification.PRIORITY_LOW,
                        )
                    for ins, kb in zip(insights[:5], keyboards):
                        detail = f"<b>{ins['title']}</b>\n{ins['detail']}\n💡 {ins['action']}"
                        await notification_queue.enqueue(
                            topic="memory_patterns",
                            text=detail,
                            priority=Notification.PRIORITY_MEDIUM,
                            reply_markup=kb,
                        )
                        await asyncio.sleep(0.5)
                    # P7: sleep moved outside _overlap_guard below
                    sleep_sec = settings.memory_patterns_poll_interval
            except Exception:
                logger.exception("Patterns loop error")
        # P7: sleep outside lock — don't hold _overlap_guard during sleep
        await asyncio.sleep(sleep_sec)


from functools import partial
from src.core.infra.task_manager import task_manager

task_manager.register(
    "memory-patterns", partial(patterns_loop, settings.owner_telegram_id)
)
