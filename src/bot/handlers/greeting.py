"""Personalised greeting generator — memory facts + unread inbox → LLM greeting."""

from __future__ import annotations

import contextlib
import html
import logging
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.config import settings
from src.core.contacts.inbox_priority import rank_inbox
from src.core.memory.memory_recall import RecallResult, recall
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.llm.base import ChatMessage, TaskType
from src.llm.router import build_provider

logger = logging.getLogger(__name__)

router = Router(name="greeting")
router.message.filter(OwnerOnly())


_GREETING_SYSTEM_PROMPT = (
    "Ты — AI-ассистент Telegram. Пользователь открыл чат с тобой, и ты должен "
    "поприветствовать его персонализированно.\n\n"
    "Правила:\n"
    "1. Напиши 2-3 коротких предложения на русском языке.\n"
    "2. Если есть факты из памяти — упомяни 1-2 самых релевантных (ненавязчиво).\n"
    "3. Если есть непрочитанные сообщения — кратко скажи, кто написал и как давно.\n"
    "4. Если есть активные задачи — упомяни их количество и самую важную.\n"
    "5. Будь дружелюбным, но не навязчивым.\n"
    "6. НЕ используй маркдаун, эмодзи — чистый текст.\n"
    "7. НЕ здоровайся формально (без «Здравствуйте»). Лучше: «Привет!», «С возвращением!».\n"
    "8. НЕ упоминай что ты «ассистент» или «ИИ».\n"
    "9. Если данных мало — просто напиши короткое тёплое приветствие.\n\n"
    "Примеры:\n"
    "- «Привет! У тебя 2 непрочитанных от Оли за сегодня. Помнишь про встречу завтра в 10?»\n"
    "- «С возвращением! Иван написал час назад. Кстати, ты просил напомнить про отчёт.»\n"
    "- «Рад тебя видеть! Всё спокойно, новых сообщений нет.»"
)


def _format_facts(recall_result: RecallResult) -> str:
    """Format memory facts into a compact list for the prompt."""

    if not recall_result.facts:
        return "Нет релевантных фактов."

    lines: list[str] = []

    for f in recall_result.facts[:5]:
        lines.append(f"- {f.fact}")

    return "\n".join(lines)


def _format_inbox(inbox_items: list[dict[str, Any]]) -> str:
    """Format inbox items into a compact summary for the prompt."""

    if not inbox_items:
        return "Нет непрочитанных сообщений."

    lines: list[str] = []

    for item in inbox_items[:3]:
        name = item.get("peer_name", "Неизвестный")

        hours = item.get("hours_unreplied", 0)

        preview = item.get("last_message", "")

        preview_short = preview[:60] if preview else ""

        lines.append(
            f"- {name}: {hours:.0f}ч назад"
            + (f" «{preview_short}»" if preview_short else "")
        )

    return "\n".join(lines)


def _format_tasks(task_facts: list) -> str:
    """Format task facts into a compact list for the prompt."""
    if not task_facts:
        return "Нет активных задач."

    lines: list[str] = []
    for f in task_facts[:5]:
        lines.append(f"- {f.fact}")

    return "\n".join(lines)


def _build_manual_greeting(
    last_topic: str | None,
    inbox_count: int,
    task_count: int,
) -> str:
    """Build a manual Russian greeting when LLM is unavailable."""
    parts: list[str] = []

    if last_topic:
        parts.append(f"С возвращением! В прошлый раз мы обсуждали: {last_topic}.")
    else:
        parts.append("С возвращением!")

    count_parts: list[str] = []
    if inbox_count > 0:
        count_parts.append(f"{inbox_count} непрочитанных в инбоксе")
    if task_count > 0:
        count_parts.append(f"{task_count} активных задач")

    if count_parts:
        parts.append("У тебя " + " и ".join(count_parts) + ".")

    # Escape HTML so the greeting can be safely inserted into HTML parse_mode.
    return html.escape(" ".join(parts), quote=False)


async def generate_personalized_greeting(
    telegram_id: int,
) -> str:
    """Generate a personalised greeting using memory facts and unread inbox.


    Returns an empty string if the feature is disabled, no data is available,


    or any error occurs (fail-safe — never blocks /start or /help).


    Args:


        telegram_id: The Telegram user ID of the owner.


    Returns:


        A personalised greeting string (2-3 sentences, Russian) or "".


    """

    # Feature gate — global config flag

    if not settings.personalized_greeting_enabled:
        return ""

    recall_result: RecallResult | None = None

    inbox_items: list[dict[str, Any]] = []

    # 1. Load memory facts (fail-safe: any exception → empty)

    try:
        recall_result = await recall(
            telegram_id,
            limit=5,
            mode="light",
            include_self=True,
            include_pinned=True,
            include_tasks=True,
            include_deep=False,
        )

    except Exception:
        logger.debug("greeting: memory recall failed", exc_info=True)

    # 2. Load inbox priority (fail-safe: any exception → empty)

    try:
        inbox_items = await rank_inbox(owner_telegram_id=telegram_id, limit=3)

    except Exception:
        logger.debug("greeting: inbox priority failed", exc_info=True)

    # 3. Separate facts and compute counts

    task_facts: list = []
    regular_facts: list = []
    if recall_result is not None:
        for f in recall_result.facts:
            if f.reason == "📋 активная задача":
                task_facts.append(f)
            else:
                regular_facts.append(f)

    task_count = len(task_facts)
    inbox_count = len(inbox_items)
    last_topic: str | None = regular_facts[0].fact if regular_facts else None

    has_data = bool(regular_facts) or inbox_count > 0 or task_count > 0
    if not has_data:
        return ""

    # 4. Build prompt context

    facts_text = (
        _format_facts(recall_result) if recall_result else "Нет релевантных фактов."
    )

    inbox_text = _format_inbox(inbox_items)

    task_text = _format_tasks(task_facts)

    user_prompt = (
        "Факты из памяти пользователя:\n"
        f"{facts_text}\n\n"
        "Непрочитанные сообщения:\n"
        f"{inbox_text}\n\n"
        "Активные задачи:\n"
        f"{task_text}\n\n"
        "Сгенерируй персонализированное приветствие."
    )

    # 5. Call LLM (fail-safe: any exception → "")

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, telegram_id)

            provider = await build_provider(session, owner, task_type=TaskType.DEFAULT)

            if provider is None:
                logger.debug(
                    "greeting: no LLM provider available for user %d", telegram_id
                )

                return _build_manual_greeting(last_topic, inbox_count, task_count)

            messages: list[ChatMessage] = [
                ChatMessage(role="system", content=_GREETING_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ]

            try:
                greeting_text = await provider.chat(
                    messages, task_type=TaskType.DEFAULT
                )

            finally:
                # Release HTTP connections — provider is cached so

                # close() is a no-op on the factory, but defence-in-depth.

                with contextlib.suppress(Exception):
                    await provider.close()
            greeting_text = (greeting_text or "").strip()

            # Sanity: if LLM returned something weird, fall back to empty
            if len(greeting_text) < 5:
                logger.debug(
                    "greeting: LLM returned too short text for user %d: %r",
                    telegram_id,
                    greeting_text,
                )
                return _build_manual_greeting(last_topic, inbox_count, task_count)

            # Telegram message limit is 4096 chars. Truncate if LLM hallucinated
            # a very long response (prompt asks for 2-3 sentences, so >2000 is abnormal).
            if len(greeting_text) > 4000:
                logger.warning(
                    "greeting: LLM returned abnormally long text for user %d (%d chars), "
                    "truncating to 4000",
                    telegram_id,
                    len(greeting_text),
                )
                greeting_text = greeting_text[:3997] + "..."

            return html.escape(greeting_text, quote=False)

    except Exception:
        logger.debug("greeting: LLM call failed", exc_info=True)

        return _build_manual_greeting(last_topic, inbox_count, task_count)


@router.message(Command("greet"))
async def cmd_greet(message: Message) -> None:
    """Handle /greet — generate a personalised LLM greeting."""
    if not message.from_user:
        return

    tg_id = message.from_user.id
    greeting_text = await generate_personalized_greeting(tg_id)

    if greeting_text:
        await message.answer(greeting_text)
    else:
        await message.answer(
            "Пока нет данных для персонализированного приветствия. "
            "Напиши что-нибудь, чтобы я запомнил."
        )
