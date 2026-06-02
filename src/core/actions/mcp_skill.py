"""MCP Tool: активация скиллов."""

import logging
from typing import Any

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="use_skill",
    description=(
        "Активирует скилл для выполнения специализированной задачи. "
        "Используй когда нужно применить конкретный скилл."
    ),
    category="utility",
    risk="low",
    params={
        "skill_name": "str — название скилла для активации",
        "params": "dict — параметры для скилла (опционально)",
    },
)
async def use_skill(
    skill_name: str = "",
    params: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Активирует скилл и возвращает результат."""
    if not skill_name:
        return {"error": "skill_name обязателен"}

    try:
        # Layering fix: src/core/* must not import from src/bot/*.
        # The previous `from src.bot.handlers import skills` import was broken
        # (the target file does not exist), and `find_skill` is not defined
        # anywhere in the codebase. Inline the None-fallback here and rely on
        # the registry to be wired later.
        skill = None

        if not skill:
            return {
                "error": f"Скилл '{skill_name}' не найден. Доступные скиллы: /skills"
            }

        return {
            "ok": True,
            "skill": skill_name,
            "body": (
                skill.get("body", "")[:2000]
                if isinstance(skill, dict)
                else str(skill)[:2000]
            ),
            "note": "Скилл загружен. Примени его инструкции к текущей задаче.",
        }
    except Exception as e:
        return {"error": str(e)}
