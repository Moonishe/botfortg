"""Prompt-level procedural skills for Asist.

V1 skills are not executable plugins. They are compact reusable procedures
injected into prompts when their triggers match the current request.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterable

from src.config import settings
from src.core.scheduling.notification_queue import notification_queue
from src.db.models import Skill, Trajectory
from src.db.repo import (
    add_skill_usage,
    get_or_create_user,
    list_skills,
    upsert_skill,
)
from src.db.session import get_session

logger = logging.getLogger(__name__)


def _matches(text: str, patterns: Iterable[str] | None) -> int:
    if not patterns:
        return 0
    score = 0
    low = text.lower()
    for pattern in patterns:
        if not pattern:
            continue
        p = str(pattern).strip()
        if not p:
            continue
        try:
            if re.search(p, text, flags=re.IGNORECASE):
                score += 3
                continue
        except re.error:
            pass
        if p.lower() in low:
            score += 2
    return score


async def list_relevant_skills(
    telegram_id: int,
    user_text: str,
    route_mode: str | None = None,
    limit: int = 5,
) -> list[Skill]:
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        skills = await list_skills(
            session,
            owner,
            enabled=True,
            review_status="approved",
            limit=100,
        )

    ranked: list[tuple[int, Skill]] = []
    for skill in skills:
        patterns = skill.trigger_patterns_json or []
        score = _matches(user_text, patterns)
        if route_mode and route_mode.lower() in [str(p).lower() for p in patterns]:
            score += 2
        if score:
            ranked.append((score + (skill.success_count or 0), skill))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [skill for _, skill in ranked[:limit]]


def format_skill_index(skills: list[Skill]) -> str:
    if not skills:
        return ""
    lines = ["<skill_index>"]
    for skill in skills[:5]:
        desc = (skill.description or "").strip()
        header = f"- {skill.name}"
        if desc:
            header += f": {desc[:160]}"
        lines.append(header)
        body = (skill.body or "").strip()
        if body:
            lines.append(f"  procedure: {body[:700]}")
    lines.append("</skill_index>")
    return "\n".join(lines)


async def build_skill_index(
    telegram_id: int,
    user_text: str,
    route_mode: str | None = None,
    limit: int = 5,
) -> tuple[str, list[dict]]:
    from src.core.context_cache import get as cache_get

    cached = await cache_get(f"skills:{telegram_id}:{route_mode}")
    if cached is not None:
        return cached

    skills = await list_relevant_skills(telegram_id, user_text, route_mode, limit)
    result = (
        format_skill_index(skills),
        [{"id": s.id, "name": s.name, "route_mode": route_mode} for s in skills],
    )

    from src.core.context_cache import put as cache_put

    await cache_put(f"skills:{telegram_id}:{route_mode}", result, ttl=30)
    return result


async def record_skill_usage(
    telegram_id: int,
    skill_id: int,
    trajectory_id: int | None,
    success: bool,
) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        skill = await session.get(Skill, skill_id)
        if skill is None or skill.user_id != owner.id:
            return
        await add_skill_usage(
            session,
            owner,
            skill,
            trajectory_id=trajectory_id,
            success=success,
        )


async def record_skill_usages(
    telegram_id: int,
    used_skills: list[dict] | None,
    trajectory_id: int | None,
    success: bool,
) -> None:
    if not used_skills:
        return
    for item in used_skills:
        skill_id = item.get("id") if isinstance(item, dict) else None
        if skill_id:
            await record_skill_usage(telegram_id, int(skill_id), trajectory_id, success)


def _safe_skill_name(route_mode: str, intent_name: str) -> str:
    base = f"{route_mode or 'general'}_{intent_name or 'chat'}"
    base = re.sub(r"[^a-zA-Z0-9_а-яА-Я-]+", "_", base).strip("_")
    return base[:96] or "general_chat"


async def suggest_skills_from_trajectories(telegram_id: int) -> int:
    """Create low-risk pending skills from repeated successful trajectories."""
    from sqlalchemy import select

    since = datetime.now(timezone.utc) - timedelta(days=1)
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        rows = (
            (
                await session.execute(
                    select(Trajectory)
                    .where(
                        Trajectory.user_id == owner.id,
                        Trajectory.success,
                        Trajectory.created_at >= since,
                    )
                    .order_by(Trajectory.created_at.desc())
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )

        buckets: Counter[tuple[str, str]] = Counter()
        examples: dict[tuple[str, str], Trajectory] = {}
        for row in rows:
            intent = row.intent_json or {}
            intent_name = str(intent.get("intent") or "chat")
            key = (row.route_mode or "unknown", intent_name)
            buckets[key] += 1
            examples.setdefault(key, row)

        created = 0
        for (route_mode, intent_name), count in buckets.items():
            if count < 3:
                continue
            name = _safe_skill_name(route_mode, intent_name)
            existing = [
                s
                for s in await list_skills(session, owner, limit=200)
                if s.name == name
            ]
            if existing:
                continue
            sample = examples[(route_mode, intent_name)]
            body = (
                f"When route_mode={route_mode} and intent={intent_name}, prefer the "
                "shortest successful path used in recent trajectories. Preserve user "
                "intent, avoid inventing contacts, and ask a clarify question when "
                "required inputs are missing."
            )
            await upsert_skill(
                session,
                owner,
                name=name,
                description=f"Auto-suggested from {count} successful recent turns.",
                trigger_patterns_json=[
                    route_mode,
                    intent_name,
                    sample.request_text[:80],
                ],
                body=body,
                enabled=False,
                review_status="pending",
            )
            created += 1
        return created


async def propose_skills_from_analysis(owner_id: int) -> list[dict]:
    """Вызывает skill_creator агента через LLM и создаёт предложенные навыки.

    Фильтрует предложения по confidence > 0.7 и авто-создаёт их
    сразу активными (enabled=True, review_status="approved").
    """
    from src.agents.skill_creator_agent import propose as agent_propose
    from src.db.repo import fetch_my_messages_global
    from src.llm.router import build_provider

    proposals: list[dict] = []
    created_skills: list[dict] = []

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, owner_id)
            messages_raw = await fetch_my_messages_global(session, owner, limit=50)
            recent_messages = [
                {
                    "text": msg.text or "",
                    "is_outgoing": msg.is_outgoing
                    if hasattr(msg, "is_outgoing")
                    else True,
                    "timestamp": str(msg.date) if hasattr(msg, "date") else "",
                }
                for msg in messages_raw
            ]

            if not recent_messages:
                logger.debug("propose_skills_from_analysis: no messages to analyze")
                return []

            provider = await build_provider(session, owner)
            proposals = await agent_propose(provider, recent_messages)
    except Exception:
        logger.exception("propose_skills_from_analysis: agent call failed")
        return []

    # Фильтруем по confidence и создаём навыки
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            confidence = proposal.get("confidence", 0)
            if not isinstance(confidence, (int, float)) or confidence <= 0.7:
                continue

            name = str(proposal.get("name", "")).strip()
            if not name:
                continue

            # Проверяем, нет ли уже навыка с таким именем
            existing = [
                s
                for s in await list_skills(session, owner, limit=200)
                if s.name.lower() == name.lower()
            ]
            if existing:
                logger.debug(
                    "propose_skills_from_analysis: skill %r already exists", name
                )
                continue

            try:
                skill = await upsert_skill(
                    session,
                    owner,
                    name=name[:128],
                    description=str(proposal.get("description", "")),
                    trigger_patterns_json=proposal.get("trigger_patterns") or [],
                    body=str(proposal.get("body", "")),
                    enabled=True,
                    review_status="approved",
                )
                created_skills.append(
                    {
                        "name": name,
                        "id": skill.id,
                        "confidence": confidence,
                    }
                )
                logger.info(
                    "propose_skills_from_analysis: created skill %r (confidence=%.2f)",
                    name,
                    confidence,
                )
            except Exception:
                logger.exception(
                    "propose_skills_from_analysis: failed to upsert skill %r", name
                )

    return created_skills


async def skill_optimizer_loop(telegram_id: int) -> None:
    _last_skill_creation_run: float = 0

    while True:
        try:
            created = await suggest_skills_from_trajectories(telegram_id)
            if created:
                await notification_queue.enqueue(
                    topic="skills",
                    category="self_evolution",
                    priority=2,
                    text=f"Found {created} new skill suggestions. Open /evolve.",
                )
        except Exception:
            logger.exception("skill_optimizer_loop trajectory analysis failed")

        # Feature 1: Skill Creator agent — автономный, каждый час
        try:
            now = asyncio.get_event_loop().time()
            if now - _last_skill_creation_run >= 3600:
                _last_skill_creation_run = now
                proposed = await propose_skills_from_analysis(telegram_id)
                if proposed:
                    names = [s["name"] for s in proposed]
                    await notification_queue.enqueue(
                        topic="skills",
                        category="self_evolution",
                        priority=3,
                        text=(
                            f"Skill Creator создал {len(proposed)} навыков: "
                            f"{', '.join(names[:5])}. "
                            f"Уже активны в /skills."
                        ),
                    )
        except Exception:
            logger.exception("skill_optimizer_loop skill creator analysis failed")

        await asyncio.sleep(settings.skill_optimizer_interval_sec)


from functools import partial
from src.core.infra.task_manager import task_manager

task_manager.register(
    "skill-optimizer", partial(skill_optimizer_loop, settings.owner_telegram_id)
)
