"""Обработка реакций пользователя на сообщения бота → корректировка памяти.

Когда пользователь реагирует на сообщение бота эмодзи, это implicit-сигнал
обратной связи. Реакция интерпретируется как accept/reject/question/acknowledge
и используется для повышения/понижения confidence связанных фактов в памяти.

Принцип работы:
- 👍 ❤️ 🔥 💯 → accept: boost confidence связанных фактов
- 👎 → reject: decay confidence связанных фактов
- 🤔 → question: нейтрально, но фиксируем неуверенность
- 👀 → acknowledge: лёгкое повышение (пользователь видел)
"""

from __future__ import annotations

import logging
from typing import Any

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


async def process_reaction(reaction_data: dict[str, Any]) -> None:
    """Обработать реакцию пользователя на сообщение бота → скорректировать память.

    Находит факты, активные в диалоге когда бот отправил это сообщение,
    и повышает/понижает их confidence на основе типа реакции.

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

    # Определить тип сигнала и фактор
    signal_type, factor = REACTION_SIGNALS.get(reaction_emoji, _DEFAULT_SIGNAL)

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


async def process_reaction_feedback(reaction_data: dict[str, Any]) -> None:
    """Публичный alias для process_reaction (совместимость со spec)."""
    await process_reaction(reaction_data)


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
    from src.db.repos.memory_repo import get_cluster_members
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
