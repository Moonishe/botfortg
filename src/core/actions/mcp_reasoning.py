"""MCP Tool: пошаговое логическое рассуждение с инструментами и самокоррекцией.

Экспонирует CoTEngine как инструмент, доступный Maestro и другим агентам.
Позволяет решать сложные задачи через цепочку размышлений (Chain-of-Thought).
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="think_step_by_step",
    description=(
        "Пошаговое логическое рассуждение с вызовом инструментов. "
        "Используй для сложных задач, требующих нескольких шагов анализа, "
        "поиска фактов и самопроверки. Движок сам вызывает нужные инструменты, "
        "отслеживает результаты и исправляет ошибки."
    ),
    category="reasoning",
    risk="low",
    params={
        "problem": "str – задача, которую нужно решить пошагово",
        "plan_id": "str|None – опциональный ID готового плана (scaffolding)",
    },
    input_schema={
        "type": "object",
        "properties": {
            "problem": {
                "type": "string",
                "description": "Задача для пошагового решения (текст).",
            },
            "plan_id": {
                "type": "string",
                "description": "ID готового плана из HTN-планировщика (опционально).",
            },
        },
        "required": ["problem"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "ok": {
                "type": "boolean",
                "description": "Успешно ли завершено рассуждение.",
            },
            "steps": {
                "type": "integer",
                "description": "Количество выполненных шагов.",
            },
            "solved": {
                "type": "boolean",
                "description": "Решена ли задача (confidence > 0.8).",
            },
            "answer": {
                "type": "string",
                "description": "Финальный ответ или None.",
            },
            "total_tool_calls": {
                "type": "integer",
                "description": "Общее количество вызовов инструментов.",
            },
        },
    },
)
async def mcp_reasoning(
    problem: str,
    plan_id: str | None = None,
    *,
    session: Any = None,
    user: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Решить задачу через пошаговое рассуждение с инструментами.

    Вызывает CoTEngine.reason() и возвращает структурированный результат.
    """
    from src.core.reasoning.cot_engine import CoTEngine

    # Собираем контекст из runtime-аргументов
    context: dict[str, Any] = {
        "source": "mcp_reasoning",
        "user_id": getattr(user, "telegram_id", None) if user is not None else None,
    }
    # Пробрасываем дополнительные контекстные данные, если переданы
    context.update({k: v for k, v in kwargs.items() if not k.startswith("_")})

    # Резолвим план, если передан plan_id
    plan: dict | None = None
    if plan_id:
        try:
            from src.core.reasoning.htn_planner import HTNPlanner  # type: ignore[import-untyped]

            from src.core.reasoning.htn_planner import get_plan as _get_plan
            from src.db.repo import get_or_create_user

            plan_store = _get_plan(owner_id=user.id if hasattr(user, "id") else user)
        except Exception:
            logger.debug("Не удалось загрузить план %s, продолжаем без плана", plan_id)
            plan = None

    engine = CoTEngine()

    try:
        trace = await engine.reason(
            problem=problem,
            context=context,
            session=session,
            user=user,
            plan=plan,
        )
    except Exception:
        logger.exception("CoT reasoning упал для задачи: %s", problem[:200])
        return {
            "ok": False,
            "error": "Reasoning engine failed",
            "steps": 0,
            "solved": False,
            "answer": None,
            "total_tool_calls": 0,
        }

    return {
        "ok": True,
        "steps": len(trace.steps),
        "solved": trace.solved,
        "answer": trace.final_answer,
        "total_tool_calls": trace.total_tool_calls,
        "total_iterations": trace.total_iterations,
    }
