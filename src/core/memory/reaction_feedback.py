"""Обработка реакций пользователя на сообщения бота → корректировка памяти.

Когда пользователь реагирует на сообщение бота эмодзи, это implicit-сигнал
обратной связи. Реакция интерпретируется как accept/reject/question/acknowledge
и используется для повышения/понижения confidence связанных фактов в памяти.

Принцип работы:
- 👍 ❤️ 🔥 💯 → accept: boost confidence связанных фактов
- 👎 → reject: decay confidence связанных фактов
- 🤔 → question: нейтрально, но фиксируем неуверенность
- 👀 → acknowledge: лёгкое повышение (пользователь видел)

Некоторые реакции (🤔, 👎, 🤨) также генерируют проактивный follow-up —
бот отправляет уточняющее сообщение через очередь уведомлений.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from src.core.humanizer.humanizer import humanize_response as _humanize_response
from src.core.learning.preference_learner import preference_learner

logger = logging.getLogger(__name__)

# ── Карта реакций: (тип_сигнала, фактор_коррекции) ─────────────────────────
REACTION_SIGNALS: dict[str, tuple[str, float]] = {
    "\U0001f44d": ("accept", 0.3),  # 👍 boost +30%
    "\u2764\ufe0f": ("accept", 0.5),  # ❤️ сильный boost +50%
    "\U0001f525": ("accept", 0.4),  # 🔥 boost +40%
    "\U0001f4af": ("accept", 0.5),  # 💯 сильный boost +50%
    "\U0001f44e": ("reject", 0.3),  # 👎 decay -30%
    "\U0001f914": ("question", 0.0),  # 🤔 нейтрально, фиксируем неуверенность
    "\U0001f440": ("acknowledge", 0.1),  # 👀 лёгкое повышение
    # Остальные — мягкий accept по умолчанию
    "\U0001f601": ("accept", 0.15),  # 😁
    "\U0001f389": ("accept", 0.15),  # 🎉
    "\U0001f64f": ("accept", 0.15),  # 🙏
}

# Дефолтный сигнал для неизвестных реакций
_DEFAULT_SIGNAL: tuple[str, float] = ("acknowledge", 0.05)

# ── Smart follow-up: реакции → текст проактивного уточнения ────────────────
_SMART_FOLLOWUP_REACTIONS: dict[str, str] = {
    "🤔": "Что не так? Я могу уточнить или исправить?",
    "👎": "Понял, учту. Что именно не так?",
    "🤨": "Есть сомнения? Могу перепроверить информацию.",
    "😢": "Понимаю. Хочешь обсудить это?",
    "🙏": "Рад помочь! Если что-то ещё — я здесь.",
    "💔": "Сожалею. Могу чем-то помочь?",
}


async def process_reaction(reaction_data: dict[str, Any]) -> None:
    """Обработать реакцию пользователя на сообщение бота → скорректировать память.

    Находит факты, активные в диалоге когда бот отправил это сообщение,
    и повышает/понижает их confidence на основе типа реакции.

    Для smart-followup реакций (🤔, 👎, 🤨) также ставит в очередь
    проактивное уточняющее сообщение через notification_queue.

    Args:
        reaction_data: Словарь с ключами:
            - message_id: ID сообщения
            - chat_id: ID чата
            - reactor_id: ID пользователя, поставившего реакцию
            - reaction: эмодзи реакции (строка)
            - timestamp: время реакции
    """
    reaction_emoji = reaction_data.get("reaction", "")
    reactor_id = reaction_data.get("reactor_id")

    if not reaction_emoji or reactor_id is None:
        return

    # Определить тип сигнала
    signal_type, base_factor = REACTION_SIGNALS.get(reaction_emoji, _DEFAULT_SIGNAL)

    # Построить контекст для PreferenceLearner
    # Ищем факты, связанные с этим чатом и сообщением
    context: dict[str, Any] = {
        "source": "reaction_feedback",
        "chat_id": reaction_data.get("chat_id"),
        "message_id": reaction_data.get("message_id"),
        "reaction": reaction_emoji,
        "signal_type": signal_type,
    }

    # Пытаемся найти связанные memory_ids через историю диалога
    try:
        memory_ids = await _find_related_memory_ids(
            reactor_id=reactor_id,
            chat_id=reaction_data.get("chat_id"),
            message_id=reaction_data.get("message_id"),
        )
        if memory_ids:
            context["memory_ids"] = memory_ids
    except Exception:
        logger.debug(
            "Не удалось найти связанные memory_ids для реакции %s",
            reaction_emoji,
        )

    # Делегировать PreferenceLearner
    try:
        result = await preference_learner.learn(
            signal_type=signal_type,
            context=context,
            user_id=reactor_id,
        )
        logger.debug(
            "Reaction feedback: reaction=%s signal=%s updated=%d",
            reaction_emoji,
            signal_type,
            result.get("updated", 0),
        )
    except Exception:
        logger.exception(
            "Ошибка обработки reaction feedback: reaction=%s user=%d",
            reaction_emoji,
            reactor_id,
        )

    # Проактивный follow-up для smart-реакций
    _followup_text = _SMART_FOLLOWUP_REACTIONS.get(reaction_emoji)
    if _followup_text is not None:
        # Применить humanizer для естественного тона
        _followup_text = await asyncio.to_thread(
            _humanize_response, _followup_text, context_hint="memory"
        )
        try:
            from src.core.scheduling.notification_queue import notification_queue
            from src.db.models import Notification as _NotifModel

            chat_id = reaction_data.get("chat_id", "?")
            msg_id = reaction_data.get("message_id", "?")
            await notification_queue.enqueue(
                topic="reaction_followup",
                text=(
                    f"💬 Реакция {reaction_emoji} на сообщение бота "
                    f"(чат {chat_id}, сообщение {msg_id})\n"
                    f"→ {_followup_text}"
                ),
                priority=_NotifModel.PRIORITY_MEDIUM,
                category="smart_followup",
            )
            logger.debug(
                "Smart followup enqueued: reaction=%s chat=%s msg=%s",
                reaction_emoji,
                chat_id,
                msg_id,
            )
        except Exception:
            logger.exception(
                "Не удалось поставить smart-followup в очередь: reaction=%s",
                reaction_emoji,
            )


async def process_reaction_feedback(reaction_data: dict[str, Any]) -> None:
    """Публичный alias для process_reaction (совместимость со spec)."""
    await process_reaction(reaction_data)


# ── A7: NL memory feedback — text replies as corrections/supplements ──────
# ponytail: regex patterns for 3 NL feedback types, upgrade to LLM classification if accuracy <80%.

_NL_SUPPLEMENT_RE = re.compile(
    r"(?i)(?:ещё|ещ[её]|кстати|а\s+ ещё|дополнительно|точнее)"
    r".{0,60}"
)
_NL_REPLACE_RE = re.compile(
    r"(?i)(?:забудь|забудьте|игнорируй|нет\s*,\s*не\s+так|неправильно|неверно)"
    r".{0,60}"
)


async def process_nl_feedback(
    text: str,
    reactor_id: int,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> dict[str, Any] | None:
    """Detect NL memory feedback in a text reply to a bot message.

    Classifies the reply as:
    - "correcting": negates or replaces a fact (routes to detect_memory_correction)
    - "supplementing": adds info to existing fact
    - "replacing": full replace ("забудь это, вот правильный вариант")

    Returns:
        dict with 'action', 'keywords', 'new_text' — or None if no NL feedback detected.
    """
    text_clean = text.strip()
    if len(text_clean) < 5:
        return None

    # Try existing detect_memory_correction first (handles negation patterns).
    try:
        from src.core.contacts.smart_reply import detect_memory_correction

        correction = detect_memory_correction(text_clean)
        if correction is not None:
            return {
                "action": "correcting",
                "source": "detect_memory_correction",
                **correction,
            }
    except Exception:
        logger.debug("detect_memory_correction failed", exc_info=True)

    # Supplement: "ещё ...", "кстати ...", "точнее ..."
    if _NL_SUPPLEMENT_RE.search(text_clean):
        keywords = re.findall(r"[а-яёa-z]{4,}", text_clean.lower())
        return {
            "action": "supplementing",
            "keywords": list(set(keywords))[:5],
            "new_text": text_clean,
        }

    # Replace: "забудь ...", "неправильно ...", "неверно ..."
    if _NL_REPLACE_RE.search(text_clean):
        keywords = re.findall(r"[а-яёa-z]{4,}", text_clean.lower())
        return {
            "action": "replacing",
            "keywords": list(set(keywords))[:5],
            "new_text": text_clean,
        }

    return None


async def _find_related_memory_ids(
    reactor_id: int,
    chat_id: int | None,
    message_id: int | None,
) -> list[int]:
    """Найти memory_ids фактов, связанных с диалогом, где была реакция.

    Ищет недавние сообщения бота в этом чате и связанные с ними факты памяти.
    """
    if chat_id is None:
        return []

    from src.db.models._memory import Memory
    from src.db.session import get_session
    from sqlalchemy import select

    async with get_session() as session:
        # Находим недавние активные факты для этого пользователя
        result = await session.execute(
            select(Memory.id)
            .where(
                Memory.user_id == reactor_id,
                Memory.is_active == True,
            )
            .order_by(Memory.updated_at.desc().nullslast())
            .limit(10)
        )
        return [row[0] for row in result.fetchall()]
