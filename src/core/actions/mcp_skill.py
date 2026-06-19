"""MCP Tool: активация скиллов."""

import logging
from typing import Any

from src.core.actions.tool_registry import tool
from src.core.infra.key_guard import safe_str

logger = logging.getLogger(__name__)


def _resolve_user_id(kwargs: dict[str, Any]) -> int | None:
    """Извлекает telegram_id из runtime kwargs (User ORM или int)."""
    _user_val = kwargs.get("user")
    if _user_val is None:
        return None
    if hasattr(_user_val, "telegram_id"):
        return int(_user_val.telegram_id)  # type: ignore[union-attr]
    try:
        return int(_user_val)
    except (TypeError, ValueError):
        return None


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
    """Активирует скилл и возвращает его полное содержимое."""
    if not skill_name:
        return {"error": "skill_name обязателен"}

    telegram_id = _resolve_user_id(kwargs)
    if telegram_id is None:
        return {"error": "Не удалось определить пользователя для поиска скилла"}

    try:
        from src.db.repos.skill_repo import get_skill_by_name
        from src.db.repo import get_or_create_user
        from src.db.session import get_session

        async with get_session() as session:
            owner = await get_or_create_user(session, telegram_id)
            skill = await get_skill_by_name(session, owner, skill_name)

        if skill is None:
            return {
                "error": f"Скилл '{skill_name}' не найден. "
                "Используй /skills для просмотра доступных скиллов."
            }

        return {
            "ok": True,
            "skill": skill.name,
            "description": skill.description or "",
            "body": (skill.body or "")[:2000],
            "note": "Скилл загружен. Примени его инструкции к текущей задаче.",
        }
    except Exception as e:
        logger.exception("use_skill failed for '%s'", skill_name)
        return {"error": safe_str(e)}
