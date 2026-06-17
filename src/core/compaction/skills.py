"""Skill extraction from successful trajectories for the Compaction Pipeline."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import exists, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.infra.text_sanitizer import sanitize_html

if TYPE_CHECKING:
    from src.llm.base import LLMProvider

logger = logging.getLogger(__name__)


async def extract_skills_from_trajectories(
    session: AsyncSession,
    user_id: int,
    *,
    limit: int = 20,
    llm_provider: LLMProvider | None = None,
) -> list:
    """Find successful complex trajectories and propose skills from them.

    Returns a list of proposed Skill rows (already created with
    review_status='proposed').
    """
    from src.db.models import Skill, SkillUsage, Trajectory

    min_age_days = getattr(settings, "compaction_trajectory_min_age_days", 7)
    min_calls = getattr(settings, "compaction_trajectory_min_calls", 3)
    cutoff = datetime.now(UTC) - timedelta(days=min_age_days)

    stmt = (
        select(Trajectory)
        .where(
            Trajectory.user_id == user_id,
            Trajectory.success.is_(True),
            Trajectory.created_at < cutoff,
            func.coalesce(func.json_array_length(Trajectory.actions_json), 0)
            >= min_calls,
            not_(
                exists().where(
                    SkillUsage.trajectory_id == Trajectory.id,
                )
            ),
        )
        .order_by(Trajectory.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    trajectories = list(result.scalars().all())
    if not trajectories:
        return []

    prompt_parts = ["Успешные траектории пользователя:"]
    for t in trajectories:
        actions = t.actions_json or []
        prompt_parts.append(
            f"Запрос: {t.request_text[:200]}\n"
            f"Действия: {len(actions)} tool calls\n"
            f"Ответ: {(t.response_text or '')[:200]}"
        )
    prompt_parts.append(
        "Опиши reusable навык (процедуру), который можно выделить из этих примеров. "
        'Верни ТОЛЬКО JSON: {"name": "...", "description": "...", "body": "..."}'
    )
    user_prompt = "\n\n".join(prompt_parts)

    proposed = []
    if llm_provider is None:
        return proposed

    try:
        from src.llm.base import ChatMessage

        raw = await llm_provider.chat(
            [
                ChatMessage(
                    role="system", content="Ты — модуль извлечения навыков из опыта."
                ),
                ChatMessage(role="user", content=user_prompt),
            ],
            task_type="MEMORY",
        )
    except Exception as exc:
        logger.warning("Skill extraction LLM call failed: %s", exc)
        return proposed

    if not raw:
        return proposed

    try:
        text = str(raw).strip()
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return proposed
        name = sanitize_html(str(parsed.get("name", "") or "").strip())[:128]
        description = sanitize_html(str(parsed.get("description", "") or "").strip())[
            :500
        ]
        body = sanitize_html(str(parsed.get("body", "") or "").strip())
        if not name or not body:
            return proposed
    except (json.JSONDecodeError, ValueError):
        logger.warning("Skill extraction returned invalid JSON: %r", raw[:120])
        return proposed

    existing_result = await session.execute(
        select(Skill).where(
            Skill.user_id == user_id,
            Skill.name == name,
        )
    )
    existing_skill = existing_result.scalar_one_or_none()

    if existing_skill is None:
        skill = Skill(
            user_id=user_id,
            name=name,
            description=description or None,
            body=body,
            review_status="proposed",
            validation_score=0.8,
        )
        session.add(skill)
        await session.flush()
        proposed.append(skill)
    else:
        skill = existing_skill

    # Mark the trajectories as used so they are not re-evaluated next run.
    for trajectory in trajectories:
        usage = SkillUsage(
            user_id=user_id,
            skill_id=skill.id,
            trajectory_id=trajectory.id,
            success=True,
        )
        session.add(usage)
    await session.flush()

    logger.info(
        "Proposed skill %r from %d trajectories for user %d",
        name,
        len(trajectories),
        user_id,
    )
    return proposed
