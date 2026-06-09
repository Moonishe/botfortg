"""MCP Tools: каузальный и контрфактуальный анализ.

Инструменты:
- ``analyze_causes`` — анализ причин: «Почему я не успеваю с проектом?»
- ``analyze_counterfactual`` — контрфактуальный анализ: «Что если бы я не взял тот проект?»

Оба инструмента используют существующую инфраструктуру (эпизоды, цепочки эволюции, recall)
и делают ровно один LLM-вызов для ответа.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)


def _resolve_user_id(kwargs: dict[str, Any]) -> int | None:
    """Извлекает telegram_id из runtime kwargs.

    Поддерживает как User ORM-объект, так и telegram_id (int).
    """
    _user_val = kwargs.get("user")
    if _user_val is None:
        return None
    if hasattr(_user_val, "telegram_id"):
        return int(_user_val.telegram_id)  # type: ignore[union-attr]
    try:
        return int(_user_val)
    except (TypeError, ValueError):
        return None


def _resolve_session(kwargs: dict[str, Any]) -> Any:
    """Извлекает SQLAlchemy-сессию из runtime kwargs."""
    return kwargs.get("session")


def _resolve_user_obj(kwargs: dict[str, Any]) -> Any:
    """Извлекает User ORM-объект из runtime kwargs."""
    return kwargs.get("user")


# ── Инструменты ──────────────────────────────────────────────────────────


@tool(
    name="analyze_causes",
    description=(
        "Анализ причин события или ситуации на основе истории пользователя. "
        "Использует последние эпизоды, тренды эволюции фактов и релевантные "
        "воспоминания. Отвечает на вопросы типа: «Почему я не успеваю с проектом?», "
        "«Что привело к конфликту с коллегой?», «Почему у меня упала продуктивность?»"
    ),
    category="reasoning",
    risk="low",
    params={
        "question": "str — вопрос для анализа причин (на русском)",
    },
)
async def tool_analyze_causes(
    question: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Анализ причин: «Почему я не успеваю с проектом?»"""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user_id не определён"}

    if not question or not question.strip():
        return {"ok": False, "error": "question обязателен"}

    session = _resolve_session(kwargs)
    user_obj = _resolve_user_obj(kwargs)

    # Проверяем доступность сессии и пользователя для LLM-вызова
    if session is None or user_obj is None:
        return {
            "ok": False,
            "error": (
                "Для анализа причин требуется активная сессия и пользователь. "
                "Убедитесь, что вы авторизованы и повторите запрос."
            ),
        }

    try:
        from src.core.reasoning.causal import analyze_causes

        result = await analyze_causes(
            user_id=user_id,
            question=question.strip(),
            session=session,
            user=user_obj,
        )
        return {
            "ok": True,
            "question": question.strip(),
            "answer": result,
        }
    except Exception:
        logger.exception("tool_analyze_causes failed for user %d", user_id)
        return {"ok": False, "error": "Ошибка анализа причин"}


@tool(
    name="analyze_counterfactual",
    description=(
        "Контрфактуальный анализ: «А что если бы я поступил иначе?» "
        "Анализирует гипотетический сценарий на основе реальных исторических данных "
        "(эпизоды, тренды, факты памяти). Отвечает на вопросы типа: "
        "«Что если бы я не брал тот проект?», "
        "«Как изменилась бы ситуация, если бы я ответил иначе?», "
        "«Что было бы, если бы я начал раньше?»"
    ),
    category="reasoning",
    risk="low",
    params={
        "question": "str — вопрос в формате «Что если бы...?» (на русском)",
    },
)
async def tool_analyze_counterfactual(
    question: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Контрфактуальный анализ: «Что если бы я не взял тот проект?»"""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user_id не определён"}

    if not question or not question.strip():
        return {"ok": False, "error": "question обязателен"}

    session = _resolve_session(kwargs)
    user_obj = _resolve_user_obj(kwargs)

    # Проверяем доступность сессии и пользователя для LLM-вызова
    if session is None or user_obj is None:
        return {
            "ok": False,
            "error": (
                "Для контрфактуального анализа требуется активная сессия и пользователь. "
                "Убедитесь, что вы авторизованы и повторите запрос."
            ),
        }

    try:
        from src.core.reasoning.causal import analyze_counterfactual

        result = await analyze_counterfactual(
            user_id=user_id,
            question=question.strip(),
            session=session,
            user=user_obj,
        )
        return {
            "ok": True,
            "question": question.strip(),
            "answer": result,
        }
    except Exception:
        logger.exception("tool_analyze_counterfactual failed for user %d", user_id)
        return {"ok": False, "error": "Ошибка контрфактуального анализа"}
