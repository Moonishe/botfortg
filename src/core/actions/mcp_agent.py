"""MCP Agent Tools — исполнение и мониторинг автономных агентов.

Предоставляет инструменты для:
- ``execute_plan`` — пошаговое исполнение готового плана через AgentRuntime.
- ``check_agent_status`` — проверка статуса запущенного агента.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import settings
from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# Tool: execute_plan
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="execute_plan",
    description=(
        "Исполнить готовый пошаговый план через автономного агента.\n"
        "Принимает план (созданный plan_task) и исполняет его пошагово "
        "с контролем бюджета токенов, чекпоинтами и гейтами подтверждения.\n"
        "Поддерживает возобновление с чекпоинта через параметр resume_from."
    ),
    category="agent",
    risk="medium",
    params={
        "plan": "dict — план со steps, goal, metadata",
        "resume_from": "str|None — checkpoint_id для возобновления",
        "_confirmed": "bool — подтверждение пользователя (для is_checkpoint шагов)",
    },
)
async def mcp_execute_plan(
    plan: dict[str, Any],
    resume_from: str | None = None,
    user: Any = None,
    session: Any = None,
    context: dict[str, Any] | None = None,
    _confirmed: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Исполнить план пошагово через AgentRuntime.

    Args:
        plan: План (создан plan_task или вручную). Содержит steps, goal, ...
        resume_from: Если задан — возобновить с чекпоинта.
        user: Объект пользователя (передаётся runtime).
        session: Сессия БД (передаётся runtime).
        context: Контекст исполнения (working_memory, ...).
        _confirmed: Подтверждение пользователя для is_checkpoint шагов.

    Returns:
        dict со статусом исполнения: status, steps_completed, output, checkpoint, ...
    """
    if not settings.agent_runtime_enabled:
        return {"ok": False, "error": "Agent Runtime отключён в настройках"}

    if not plan:
        return {"ok": False, "error": "plan обязателен"}

    runtime_context = dict(context or {})

    # Пробрасываем runtime-объекты
    if user is not None:
        runtime_context["user"] = user
    if session is not None:
        runtime_context["session"] = session

    logger.info(
        "execute_plan: plan_id=%s, шагов=%d, resume_from=%s",
        plan.get("id", "?"),
        len(plan.get("steps", [])),
        resume_from,
    )

    try:
        from src.core.agents.runtime import agent_runtime

        if resume_from:
            # Возобновление с чекпоинта
            resume_result = await agent_runtime.resume(
                checkpoint_id=resume_from,
                context=runtime_context,
                session=session,
                user=user,
            )
            if not resume_result.get("ok"):
                return resume_result
            # Продолжаем с resume_step
            resume_step = resume_result.get("resume_step", 0)
            remaining_steps = plan.get("steps", [])[resume_step:]
            plan_resumed = dict(plan)
            plan_resumed["steps"] = remaining_steps
            result = await agent_runtime.run(
                plan=plan_resumed,
                context=runtime_context,
                session=session,
                user=user,
            )
            result["resumed_from"] = resume_from
            result["resume_step"] = resume_step
            return result
        else:
            # Обычный запуск
            result = await agent_runtime.run(
                plan=plan,
                context=runtime_context,
                session=session,
                user=user,
            )
            return result

    except Exception as exc:
        logger.exception("execute_plan: ошибка исполнения")
        return {
            "ok": False,
            "error": f"Ошибка исполнения плана: {exc}",
            "status": "error",
        }


# ══════════════════════════════════════════════════════════════════════════
# Tool: check_agent_status
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="check_agent_status",
    description=(
        "Проверить статус запущенного агента.\n"
        "Показывает прогресс, оставшийся бюджет токенов, "
        "ошибки и доступные чекпоинты для возобновления."
    ),
    category="agent",
    risk="low",
    params={
        "run_id": "str|None — ID запуска (если не указан — последний активный)",
    },
)
async def mcp_check_agent_status(
    run_id: str | None = None,
    user: Any = None,
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Проверить статус агента.

    Args:
        run_id: ID запуска (run_id из результата execute_plan).
        user: Объект пользователя.
        context: Контекст исполнения (содержит working_memory с чекпоинтами).

    Returns:
        dict с полями: active, run_id, checkpoints, steps_completed, errors.
    """
    if not settings.agent_runtime_enabled:
        return {"ok": True, "active": False, "message": "Agent Runtime отключён"}

    runtime_context = dict(context or {})
    wm = runtime_context.get("working_memory") or {}
    checkpoints: list[Any] = (
        wm.get("_agent_checkpoints") or runtime_context.get("_agent_checkpoints") or []
    )

    if not checkpoints:
        return {
            "ok": True,
            "active": False,
            "message": "Нет активных или сохранённых запусков агента",
            "run_id": run_id,
        }

    # Фильтруем по run_id если задан (run_id хранится в AgentState, не в чекпоинте напрямую)
    # Показываем все доступные чекпоинты
    summary_checkpoints: list[dict[str, Any]] = []
    for cp in checkpoints:
        if isinstance(cp, dict):
            summary_checkpoints.append(
                {
                    "checkpoint_id": cp.get("checkpoint_id"),
                    "step_index": cp.get("step_index"),
                    "timestamp": cp.get("timestamp"),
                }
            )
        elif hasattr(cp, "checkpoint_id"):
            summary_checkpoints.append(
                {
                    "checkpoint_id": cp.checkpoint_id,
                    "step_index": cp.step_index,
                    "timestamp": cp.timestamp.isoformat()
                    if hasattr(cp.timestamp, "isoformat")
                    else str(cp.timestamp),
                }
            )

    return {
        "ok": True,
        "active": len(checkpoints) > 0,
        "run_id": run_id,
        "checkpoints": summary_checkpoints,
        "total_checkpoints": len(checkpoints),
        "message": f"Найдено {len(checkpoints)} чекпоинтов",
    }
