"""MCP tool for manual skill generation pipeline."""

from __future__ import annotations

import logging
from typing import Any

from src.config import settings
from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="generate_skills",
    description=(
        "Запустить автоматическую генерацию новых скиллов из истории. "
        "Анализирует траектории, предлагает скиллы, авто-утверждает "
        "высоконадёжные (confidence > 0.85). "
        "Используй когда пользователь просит: 'создай новые скиллы', "
        "'обучись на истории', 'придумай новые способности'."
    ),
    category="skills",
    risk="medium",
    params={
        "force": "bool — принудительно (по умолчанию False)",
    },
)
async def generate_skills(
    force: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the skill generation pipeline.

    Uses existing curator loop: suggest → propose → auto-approve.

    Args:
        force: If True, bypass rate limits.

    Returns:
        Dict with counts: suggested, approved.
    """
    try:
        from src.core.intelligence.skills import suggest_skills_from_trajectories
        from src.core.intelligence.skills_curator import (
            auto_approve_high_confidence,
        )

        telegram_id = settings.owner_telegram_id
        if telegram_id is None:
            return {
                "ok": False,
                "error": "owner_telegram_id not configured in settings",
            }

        if force:
            logger.info("generate_skills: force=True — wider trajectory window (90d)")

        # 1. Suggest from trajectories (force widens time window to 90 days)
        suggested = await suggest_skills_from_trajectories(telegram_id, force=force)

        # 2. Auto-approve high confidence
        approved = await auto_approve_high_confidence()

        # Guard against unexpected non-int return values (defense-in-depth).
        # int(None) / int(dict) / int("abc") would crash — use safe coercion.
        def _safe_coerce_int(value: Any) -> int:
            if isinstance(value, int):
                return value
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        suggested_int = _safe_coerce_int(suggested)
        approved_int = _safe_coerce_int(approved)

        msg = (
            f"Suggested {suggested_int} skill(s), "
            f"auto-approved {approved_int} high-confidence skill(s)."
        )
        logger.info("generate_skills: %s", msg)

        return {
            "ok": True,
            "suggested": suggested_int,
            "approved": approved_int,
            "message": msg,
        }
    except Exception as exc:
        logger.exception("generate_skills failed")
        return {"ok": False, "error": str(exc)}
