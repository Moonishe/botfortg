"""MCP Planner Tools — HTN-планирование через tool-calling.

Предоставляет инструменты для создания, проверки статуса
и модификации планов через HTN Planner.

Инструменты регистрируются через декоратор ``@tool`` и доступны
LLM через стандартный tool_registry.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from src.config import settings
from src.core.actions.tool_registry import tool
from src.core.reasoning.htn_planner import (
    HTNPlanner,
    Plan,
    get_plan,
    store_plan,
    update_plan_step,
)

logger = logging.getLogger(__name__)


def _plan_to_dict(plan: Plan | None) -> dict[str, Any]:
    """Конвертирует Plan в словарь для JSON-ответа."""
    if plan is None:
        return {"ok": False, "error": "План не найден"}
    result = asdict(plan)
    result["created_at"] = plan.created_at.isoformat()
    result["ok"] = True
    result["summary"] = plan.summary()
    return result


# ══════════════════════════════════════════════════════════════════════════
# Tool: plan_task
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="plan_task",
    description=(
        "Создать пошаговый план для сложной задачи. "
        "Декомпозирует цель на подзадачи, оценивает риски, "
        "определяет зависимости между шагами и контрольные точки. "
        "Используй когда пользователь просит: 'составь план', "
        "'разбей на шаги', 'как сделать X'."
    ),
    category="reasoning",
    risk="low",
    params={
        "goal": "str",
        "context": "str|None",
    },
)
async def mcp_plan_task(
    goal: str,
    context: str | None = None,
    user: Any = None,
    session: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Создать пошаговый план для цели.

    Args:
        goal: Цель (естественный язык).
        context: Дополнительный контекст (JSON-строка или текст).
        user: Объект пользователя (передаётся runtime).
        session: Сессия БД (передаётся runtime).

    Returns:
        dict с планом и метаданными.
    """
    if not settings.htn_planner_enabled:
        return {"ok": False, "error": "HTN Planner отключён в настройках"}

    logger.info("plan_task: goal=%s", goal[:80])

    # Парсим контекст, если передан
    ctx: dict[str, Any] = {}
    if context:
        try:
            import json

            ctx = (
                json.loads(context)
                if context.strip().startswith("{")
                else {"text": context}
            )
        except (json.JSONDecodeError, ValueError):
            ctx = {"text": context}

    # Добавляем runtime-объекты в контекст
    if user is not None:
        ctx["user"] = user
    if session is not None:
        ctx["session"] = session

    try:
        planner = HTNPlanner()
        plan = await planner.plan(goal, ctx)

        # Сохраняем план
        owner_id = getattr(user, "telegram_id", None) if user is not None else None
        store_plan(owner_id, plan)

        result = _plan_to_dict(plan)
        logger.info(
            "plan_task: создан план из %d шагов, risk=%s",
            len(plan.steps),
            plan.risk,
        )
        return result

    except Exception as e:
        logger.exception("plan_task: ошибка планирования")
        return {"ok": False, "error": f"Ошибка планирования: {e}"}


# ══════════════════════════════════════════════════════════════════════════
# Tool: get_plan_status
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="get_plan_status",
    description=(
        "Проверить статус и прогресс существующего плана. "
        "Показывает текущий план, его шаги, контрольные точки "
        "и оценку оставшихся токенов."
    ),
    category="reasoning",
    risk="low",
    params={},
)
async def mcp_get_plan_status(
    user: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Проверить статус текущего плана.

    Returns:
        dict с текущим планом или сообщением об отсутствии.
    """
    owner_id = getattr(user, "telegram_id", None) if user is not None else None
    plan = get_plan(owner_id)

    if plan is None:
        return {
            "ok": True,
            "has_plan": False,
            "message": "Активного плана нет. Создайте новый через plan_task.",
        }

    return _plan_to_dict(plan)


# ══════════════════════════════════════════════════════════════════════════
# Tool: modify_plan
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="modify_plan",
    description=(
        "Изменить конкретный шаг в существующем плане. "
        "Позволяет обновить описание шага без пересоздания всего плана."
    ),
    category="reasoning",
    risk="medium",
    params={
        "step_index": "int",
        "new_description": "str",
    },
)
async def mcp_modify_plan(
    step_index: int,
    new_description: str,
    user: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Изменить шаг плана.

    Args:
        step_index: Индекс шага (0-based).
        new_description: Новое описание шага.
        user: Объект пользователя (передаётся runtime).

    Returns:
        dict с обновлённым планом или ошибкой.
    """
    if not settings.htn_planner_enabled:
        return {"ok": False, "error": "HTN Planner отключён в настройках"}

    owner_id = getattr(user, "telegram_id", None) if user is not None else None
    plan = update_plan_step(owner_id, step_index, new_description)

    if plan is None:
        return {"ok": False, "error": "План не найден или неверный индекс шага"}

    logger.info(
        "modify_plan: шаг %d изменён для owner_id=%s",
        step_index,
        owner_id,
    )
    return _plan_to_dict(plan)
