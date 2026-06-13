"""Каузальный и контрфактуальный анализ на основе существующих эпизодов и цепочек эволюции.

НЕ использует ML/DoWhy. Вся аналитика — через один LLM-вызов, который получает:
1. Последние эпизоды (episodic.py → get_recent_episodes)
2. Цепочки эволюции (evolution_chain.py → get_evolution_chain)
3. Релевантные факты памяти (memory_recall → recall)

Принцип: 1 запрос → 1 LLM-ответ → 1 строковый результат.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.llm.base import ChatMessage, TaskType
from src.llm.router import build_provider

logger = logging.getLogger(__name__)

# Максимальное число эпизодов для контекста (чтобы не перегружать промпт)
MAX_EPISODES_CONTEXT = 10
# Таймаут LLM-вызова
LLM_TIMEOUT = 45.0


def _format_episodes(episodes: list[Any]) -> str:
    """Форматирует список эпизодов в читаемый текст для промпта."""
    if not episodes:
        return "Нет данных об эпизодах."

    lines: list[str] = []
    for i, ep in enumerate(episodes[:MAX_EPISODES_CONTEXT], 1):
        summary = getattr(ep, "summary", None) or "(без сводки)"
        valence = getattr(ep, "emotional_valence", None)
        valence_str = f" [тон: {valence:.2f}]" if valence is not None else ""
        started = getattr(ep, "started_at", None)
        date_str = f" ({started.strftime('%d.%m.%Y')})" if started else ""
        lines.append(f"{i}.{date_str}{valence_str} {summary}")
    return "\n".join(lines)


def _format_evolution_chains(chains: Any) -> str:
    """Форматирует цепочки эволюции в читаемый текст для промпта."""
    if chains is None:
        return "Цепочки эволюции недоступны."

    if hasattr(chains, "summary"):
        return chains.summary()

    chain_list = getattr(chains, "chains", [])
    if not chain_list:
        return "Активных цепочек эволюции не обнаружено."

    lines: list[str] = [f"Найдено цепочек: {len(chain_list)}"]
    for i, chain in enumerate(chain_list[:5], 1):
        trend = getattr(chain, "trend", "neutral")
        length = getattr(chain, "length", 0)
        evolving = getattr(chain, "is_evolving", False)
        evo_str = " [эволюционирует]" if evolving else ""
        lines.append(f"  Цепочка {i}: длина={length}, тренд={trend}{evo_str}")
        chain_items = getattr(chain, "chain", [])[:3]
        for item in chain_items:
            fact = getattr(item, "fact", "")[:100]
            if fact:
                lines.append(f"    • {fact}")
    return "\n".join(lines)


def _format_memory_facts(recall_result: Any) -> str:
    """Форматирует факты памяти в читаемый текст для промпта."""
    if recall_result is None:
        return "Факты памяти недоступны."

    facts = getattr(recall_result, "facts", [])
    if not facts:
        return "Релевантных фактов памяти не найдено."

    lines: list[str] = []
    for f in facts[:15]:
        fact_text = getattr(f, "fact", "")
        reason = getattr(f, "reason", "")
        reason_str = f" [причина: {reason}]" if reason else ""
        if fact_text:
            lines.append(f"•{reason_str} {fact_text}")
    return "\n".join(lines) if lines else "Факты памяти пусты."


async def analyze_causes(
    user_id: int,
    question: str,
    *,
    session: Any = None,
    user: Any = None,
) -> str:
    """LLM анализирует причины события на основе эпизодов, цепочек эволюции и фактов памяти.

    Собирает контекст:
    1. Последние эпизоды (episodic.py → get_recent_episodes)
    2. Цепочки эволюции (evolution_chain.py → get_evolution_chain)
    3. Релевантные факты памяти (memory_recall → recall)

    Отправляет LLM один промпт и возвращает строковый ответ (3-5 предложений).

    Args:
        user_id: Telegram user_id пользователя.
        question: Вопрос пользователя (например, «Почему я не успеваю с проектом?»).
        session: SQLAlchemy-сессия (опционально).
        user: Объект User (опционально, для build_provider).

    Returns:
        Строка с анализом причин на русском языке.
    """
    if not question or not question.strip():
        return "Не задан вопрос для анализа."

    # ── 1. Собираем эпизоды ──
    episodes_text = "Нет данных об эпизодах."
    try:
        from src.core.memory.episodic import get_recent_episodes

        episodes = await get_recent_episodes(user_id, limit=MAX_EPISODES_CONTEXT)
        episodes_text = _format_episodes(episodes)
    except Exception:
        logger.exception(
            "analyze_causes: ошибка получения эпизодов для user %d", user_id
        )

    # ── 2. Собираем цепочки эволюции ──
    chains_text = "Цепочки эволюции недоступны."
    try:
        from src.core.memory.evolution_chain import get_evolution_chain

        chains = await get_evolution_chain(user_id)
        chains_text = _format_evolution_chains(chains)
    except Exception:
        logger.exception(
            "analyze_causes: ошибка получения цепочек эволюции для user %d", user_id
        )

    # ── 3. Собираем релевантные факты памяти ──
    facts_text = "Факты памяти недоступны."
    try:
        from src.core.memory.memory_recall import recall

        recall_result = await recall(
            telegram_id=user_id,
            query=question,
            limit=10,
            include_self=True,
            include_pinned=True,
        )
        facts_text = _format_memory_facts(recall_result)
    except Exception:
        logger.exception(
            "analyze_causes: ошибка получения фактов памяти для user %d", user_id
        )

    # ── 4. LLM-вызов ──
    provider = None
    if session is not None and user is not None:
        try:
            provider = await build_provider(
                session, user, task_type=TaskType.BACKGROUND
            )
        except Exception:
            logger.debug("analyze_causes: не удалось получить LLM-провайдера")

    if provider is None:
        # Fallback: собираем контекст без LLM и возвращаем сводку
        return (
            f"📊 **Сводка данных (без LLM-анализа)**\n\n"
            f"**Эпизоды:**\n{episodes_text}\n\n"
            f"**Тренды:**\n{chains_text}\n\n"
            f"**Факты памяти:**\n{facts_text}"
        )

    # ── Промпт ──
    prompt = (
        "🔍 **Задача: анализ причин.**\n\n"
        f"Пользователь спрашивает: «{question.strip()}»\n\n"
        "**Вот его последние эпизоды (сводки разговоров):**\n"
        f"{episodes_text}\n\n"
        "**Тренды и цепочки эволюции фактов:**\n"
        f"{chains_text}\n\n"
        "**Релевантные факты памяти:**\n"
        f"{facts_text}\n\n"
        "Проанализируй причины. Что привело к этой ситуации? "
        "Ответь 3-5 предложениями на русском языке. "
        "Основывайся только на предоставленных данных. "
        "Будь конкретен и полезен."
    )

    try:
        response = await asyncio.wait_for(
            provider.chat(
                [ChatMessage(role="user", content=prompt)],
                task_type=TaskType.BACKGROUND,
            ),
            timeout=LLM_TIMEOUT,
        )
        return response.strip()
    except TimeoutError:
        logger.warning("analyze_causes: таймаут LLM для user %d", user_id)
        return "⏱️ Анализ причин занял слишком много времени. Попробуйте позже."
    except Exception:
        logger.exception("analyze_causes: ошибка LLM для user %d", user_id)
        return "⚠️ Не удалось выполнить анализ причин из-за внутренней ошибки."


async def analyze_counterfactual(
    user_id: int,
    question: str,
    *,
    session: Any = None,
    user: Any = None,
) -> str:
    """LLM анализирует контрфактуальный сценарий «а что если?» на основе исторических данных.

    Собирает тот же контекст что и analyze_causes, но промпт нацелен на гипотетический анализ:
    «Если бы пользователь поступил иначе в [ситуация X], как бы изменился результат?»

    Args:
        user_id: Telegram user_id пользователя.
        question: Вопрос в формате «Что если бы я...?».
        session: SQLAlchemy-сессия (опционально).
        user: Объект User (опционально, для build_provider).

    Returns:
        Строка с контрфактуальным анализом на русском языке.
    """
    if not question or not question.strip():
        return "Не задан вопрос для контрфактуального анализа."

    # ── 1. Собираем эпизоды ──
    episodes_text = "Нет данных об эпизодах."
    try:
        from src.core.memory.episodic import get_recent_episodes

        episodes = await get_recent_episodes(user_id, limit=MAX_EPISODES_CONTEXT)
        episodes_text = _format_episodes(episodes)
    except Exception:
        logger.exception(
            "analyze_counterfactual: ошибка получения эпизодов для user %d", user_id
        )

    # ── 2. Собираем цепочки эволюции ──
    chains_text = "Цепочки эволюции недоступны."
    try:
        from src.core.memory.evolution_chain import get_evolution_chain

        chains = await get_evolution_chain(user_id)
        chains_text = _format_evolution_chains(chains)
    except Exception:
        logger.exception(
            "analyze_counterfactual: ошибка получения цепочек эволюции для user %d",
            user_id,
        )

    # ── 3. Собираем релевантные факты памяти ──
    facts_text = "Факты памяти недоступны."
    try:
        from src.core.memory.memory_recall import recall

        recall_result = await recall(
            telegram_id=user_id,
            query=question,
            limit=10,
            include_self=True,
            include_pinned=True,
        )
        facts_text = _format_memory_facts(recall_result)
    except Exception:
        logger.exception(
            "analyze_counterfactual: ошибка получения фактов памяти для user %d",
            user_id,
        )

    # ── 4. LLM-вызов ──
    provider = None
    if session is not None and user is not None:
        try:
            provider = await build_provider(
                session, user, task_type=TaskType.BACKGROUND
            )
        except Exception:
            logger.debug("analyze_counterfactual: не удалось получить LLM-провайдера")

    if provider is None:
        return (
            f"📊 **Сводка данных (без LLM-анализа)**\n\n"
            f"**Эпизоды:**\n{episodes_text}\n\n"
            f"**Тренды:**\n{chains_text}\n\n"
            f"**Факты памяти:**\n{facts_text}"
        )

    # ── Промпт ──
    prompt = (
        "🔄 **Задача: контрфактуальный анализ «А что если?».**\n\n"
        f"Пользователь спрашивает: «{question.strip()}»\n\n"
        "**Вот его последние эпизоды (сводки разговоров):**\n"
        f"{episodes_text}\n\n"
        "**Тренды и цепочки эволюции фактов:**\n"
        f"{chains_text}\n\n"
        "**Релевантные факты памяти:**\n"
        f"{facts_text}\n\n"
        "Проанализируй гипотетический сценарий. "
        "Если бы пользователь поступил иначе, как бы изменился результат? "
        "Основывайся ТОЛЬКО на реальных трендах и эпизодах из предоставленных данных. "
        "Ответь 3-5 предложениями на русском языке. "
        "Будь конкретен: укажи, какие именно данные подтверждают твой вывод."
    )

    try:
        response = await asyncio.wait_for(
            provider.chat(
                [ChatMessage(role="user", content=prompt)],
                task_type=TaskType.BACKGROUND,
            ),
            timeout=LLM_TIMEOUT,
        )
        return response.strip()
    except TimeoutError:
        logger.warning("analyze_counterfactual: таймаут LLM для user %d", user_id)
        return (
            "⏱️ Контрфактуальный анализ занял слишком много времени. Попробуйте позже."
        )
    except Exception:
        logger.exception("analyze_counterfactual: ошибка LLM для user %d", user_id)
        return "⚠️ Не удалось выполнить контрфактуальный анализ из-за внутренней ошибки."
