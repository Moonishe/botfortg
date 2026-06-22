"""Skills Curator — lifecycle management for proposed skills.

Provides approval/rejection workflows, auto-approval of high-confidence
skills (confidence > 0.85 stored in YAML metadata), promotion to global
scope, and a background curation loop that runs every 6 hours.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, UTC
from functools import partial
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.infra.task_manager import task_manager
from src.core.scheduling.notification_queue import notification_queue
from src.db.models import Skill
from src.db.repo import get_or_create_user, get_skill_by_name, list_skills
from src.db.session import get_session

logger = logging.getLogger(__name__)

from src.core.intelligence.skill_editor import MAX_REJECTED_EDITS

# ── Rate limiting for skill edits ──
# Key: (owner_id, skill_name_lower), Value: last edit timestamp
_edit_cooldowns: dict[tuple[int, str], datetime] = {}
_cooldown_lock = asyncio.Lock()
_overlap_guard = asyncio.Lock()  # Prevent overlapping curator_loop iterations
_COOLDOWN_TTL_SECONDS = 3600  # Evict entries older than 1 hour
MIN_USAGE_FOR_CALIBRATION = 5  # less than 5 uses → raw confidence only


# ── helpers ──────────────────────────────────────────────────────────


@asynccontextmanager
async def _curator_session(
    session: AsyncSession | None = None,
):
    """Provide a session, reusing an existing one when available.

    This lets curator functions be called both standalone (they open their own
    session) and from handlers that already have a session open.
    """
    if session is not None:
        yield session
    else:
        async with get_session() as s:
            yield s


def _get_yaml_confidence(skill: Skill) -> float:
    """Extract confidence value from skill's YAML metadata.

    The YAML frontmatter is stored as ``{"__yaml__": {...}}`` inside
    ``trigger_patterns_json``.  Returns 0.0 if not found or unparseable.
    """
    patterns = skill.trigger_patterns_json or []
    for p in patterns:
        if isinstance(p, dict) and isinstance(p.get("__yaml__"), dict):
            try:
                return float(p["__yaml__"].get("confidence", 0))
            except (TypeError, ValueError, AttributeError):
                return 0.0
    return 0.0


def _calibrate_confidence(skill: Skill) -> float:
    """Калибрует LLM-confidence на основе реального success_rate.

    Если usage_count < MIN_USAGE_FOR_CALIBRATION — возвращает raw confidence
    (недостаточно данных для калибровки).
    Если usage_count >= MIN_USAGE_FOR_CALIBRATION — смешивает raw confidence
    с success_rate.  Чем больше usage — тем больше вес success_rate.
    """
    raw = _get_yaml_confidence(skill)

    usage_count = (skill.success_count or 0) + (skill.failure_count or 0)
    if usage_count < MIN_USAGE_FOR_CALIBRATION:
        logger.debug(
            "Skill '%s' has %d uses (<%d), using raw confidence %.3f",
            skill.name,
            usage_count,
            MIN_USAGE_FOR_CALIBRATION,
            raw,
        )
        return raw

    success_rate = (skill.success_count or 0) / max(usage_count, 1)

    usage_weight = min(0.9, 0.3 + 0.03 * min(usage_count, 20))

    calibrated = raw * (1 - usage_weight) + success_rate * usage_weight
    logger.debug(
        "Skill '%s': raw=%.2f × (1-%.2f) + success_rate=%.2f × %.2f = %.3f",
        skill.name,
        raw,
        usage_weight,
        success_rate,
        usage_weight,
        calibrated,
    )
    return round(calibrated, 3)


# ── curator API ──────────────────────────────────────────────────────


async def auto_approve_high_confidence(
    owner_telegram_id: int | None = None,
) -> int:
    """Auto-approve proposed skills with confidence > 0.85.

    V2: Now includes validation gate — skills are validated against held-out
    trajectories before approval. Only skills that pass validation are approved.

    Scans all skills with ``review_status="proposed"`` for the current
    owner, checks their YAML metadata (``trigger_patterns_json["__yaml__"]``)
    for a ``confidence`` key, and approves those exceeding the 0.85
    threshold (sets ``review_status="approved"``, ``enabled=True``).

    Sends one notification to the owner summarising how many skills were
    approved.

    Args:
        owner_telegram_id: Owner Telegram ID; falls back to settings.

    Returns:
        Number of skills approved.
    """
    from src.core.intelligence.skill_validator import (
        TrajectoryData,
        validate_skill_candidate,
    )
    from src.db.models import SkillUsage, Trajectory

    _owner_id = owner_telegram_id or settings.owner_telegram_id

    async with get_session() as session:
        owner = await get_or_create_user(session, _owner_id)
        proposed = await list_skills(
            session, owner, review_status="proposed", limit=200
        )

        if not proposed:
            return 0

        # V3: Batch pre-fetch to eliminate N+1 queries per skill
        # ── Pre-fetch: recent successful trajectories ──
        since = datetime.now(UTC) - timedelta(days=7)
        traj_result = await session.execute(
            select(Trajectory)
            .where(
                Trajectory.user_id == owner.id,
                Trajectory.success.is_(True),
                Trajectory.created_at >= since,
            )
            .order_by(Trajectory.created_at.desc())
            .limit(200)
        )
        pre_fetched_trajs = [
            TrajectoryData.from_trajectory(t) for t in traj_result.scalars().all()
        ]

        # ── Pre-fetch: SkillUsage for all proposed skill IDs ──
        proposed_ids = [s.id for s in proposed]
        usage_map: dict[int, set[int]] = {}
        if proposed_ids:
            usage_result = await session.execute(
                select(SkillUsage.skill_id, SkillUsage.trajectory_id).where(
                    SkillUsage.skill_id.in_(proposed_ids),
                    SkillUsage.trajectory_id.isnot(None),
                )
            )
            for skill_id, traj_id in usage_result.all():
                usage_map.setdefault(skill_id, set()).add(traj_id)

        approved_count = 0
        rejected_count = 0
        for skill in proposed:
            confidence = _calibrate_confidence(skill)
            logger.debug(
                f"Skill {skill.name}: raw={_get_yaml_confidence(skill):.2f}, "
                f"calibrated={confidence:.2f}"
            )
            if confidence > 0.85:
                # V2: Validation gate — validate before approval
                if settings.skill_validation_enabled:
                    try:
                        validation = await validate_skill_candidate(
                            owner.id,
                            skill.name,
                            skill.body,
                            pre_fetched_trajectories=pre_fetched_trajs,
                            pre_fetched_used_ids=usage_map.get(skill.id, set()),
                            existing_skill_name=skill.name,
                        )
                    except Exception:
                        logger.exception(
                            "curator: validation failed for %r — skipping", skill.name
                        )
                        continue

                    if not validation.accepted:
                        skill.review_status = "rejected"
                        skill.enabled = False
                        note = (
                            f"\n\n[Rejected by validation gate: {validation.reason}. "
                            f"Score: {validation.score_before:.2f} → "
                            f"{validation.score_after:.2f}]"
                        )
                        skill.description = (skill.description or "") + note
                        rejected_count += 1
                        logger.info(
                            "curator: validation rejected %r — %s",
                            skill.name,
                            validation.summary,
                        )
                        continue

                    skill.validation_score = validation.score_after
                    if validation.score_delta > 0:
                        skill.best_body = skill.body

                skill.review_status = "approved"
                skill.enabled = True
                skill.updated_at = datetime.now(UTC)
                approved_count += 1

        await session.flush()

    if approved_count:
        try:
            await notification_queue.enqueue(
                topic="skills",
                category="curator",
                priority=2,
                text=(
                    f"🧠 Curator auto-approved {approved_count} skill(s) "
                    f"with confidence > 85%."
                    + (
                        f" Rejected {rejected_count} by validation gate."
                        if rejected_count
                        else ""
                    )
                ),
            )
        except Exception:
            logger.exception("curator: notification enqueue failed after auto-approval")
        logger.info(
            "curator: auto-approved %d skills, rejected %d by validation",
            approved_count,
            rejected_count,
        )

    return approved_count


async def list_proposed() -> list[dict[str, Any]]:
    """Return all proposed skills sorted by confidence (descending).

    Each entry contains skill id, name, description, extracted confidence,
    and creation timestamp.
    """
    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)
        skills = await list_skills(session, owner, review_status="proposed", limit=200)

    result: list[dict[str, Any]] = []
    for skill in skills:
        confidence = _get_yaml_confidence(skill)
        result.append(
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "confidence": confidence,
                "created_at": skill.created_at.isoformat()
                if skill.created_at
                else None,
            }
        )

    result.sort(key=lambda x: x["confidence"], reverse=True)
    return result


async def approve_skill(
    owner_id: int,
    skill_name: str,
    *,
    session: AsyncSession | None = None,
) -> bool:
    """Approve a proposed skill by name.

    Sets ``review_status="approved"`` and ``enabled=True``.

    Args:
        owner_id: Owner Telegram user ID.
        skill_name: Name of the skill to approve.
        session: Optional existing session. If provided, the caller owns the
            transaction and is responsible for committing it.

    Returns:
        True if the skill was found and updated, False otherwise.
    """
    async with _curator_session(session) as session:
        owner = await get_or_create_user(session, owner_id)
        skill = await get_skill_by_name(session, owner, skill_name)
        if skill is None:
            logger.warning(
                "curator: approve_skill %r not found for %d",
                skill_name,
                owner_id,
            )
            return False
        skill.review_status = "approved"
        skill.enabled = True
        skill.updated_at = datetime.now(UTC)
        await session.flush()

    logger.info("curator: approved skill %r (owner=%d)", skill_name, owner_id)
    return True


async def reject_skill(
    owner_id: int,
    skill_name: str,
    reason: str = "",
    *,
    session: AsyncSession | None = None,
) -> bool:
    """Reject a proposed skill.

    Sets ``review_status="rejected"`` and ``enabled=False``.
    If a *reason* is provided, it is appended to the skill description.

    V2: Also saves the rejection to the rejected-edits buffer for negative
    feedback in future optimization cycles.

    Args:
        owner_id: Owner Telegram user ID.
        skill_name: Name of the skill to reject.
        reason: Optional reason, appended to description and stored in the
            rejected-edits buffer.
        session: Optional existing session. If provided, the caller owns the
            transaction and is responsible for committing it.

    Returns:
        True if the skill was found and updated, False otherwise.
    """
    async with _curator_session(session) as session:
        owner = await get_or_create_user(session, owner_id)
        skill = await get_skill_by_name(session, owner, skill_name)
        if skill is None:
            logger.warning(
                "curator: reject_skill %r not found for %d",
                skill_name,
                owner_id,
            )
            return False
        skill.review_status = "rejected"
        skill.enabled = False
        if reason:
            note = f"\n\n[Rejected: {reason}]"
            skill.description = (skill.description or "") + note

            # V2: Save rejection to rejected-edits buffer
            rejected = skill.rejected_edits_json or []
            rejected.append(
                {
                    "op": "create",
                    "target": skill_name,
                    "content": (skill.body or "")[:200],
                    "reason": reason,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            # Keep only last 10 rejections
            skill.rejected_edits_json = rejected[-MAX_REJECTED_EDITS:]

        skill.updated_at = datetime.now(UTC)
        await session.flush()

    logger.info("curator: rejected skill %r (owner=%d)", skill_name, owner_id)
    return True


async def apply_skill_edit(
    owner_id: int,
    skill_name: str,
    edit_op: str,
    edit_target: str | None = None,
    edit_content: str = "",
    edit_reason: str = "",
    *,
    skip_validation: bool = False,
) -> dict:
    """Apply a bounded edit to an existing skill.

    Instead of replacing the entire skill body, applies a minimal targeted edit
    (append, insert_after, replace, delete) with edit budget enforcement.

    V2: SkillOpt-inspired bounded edits with validation gate.

    Args:
        owner_id: Owner user ID
        skill_name: Skill name to edit
        edit_op: Operation type (append/insert_after/replace/delete)
        edit_target: Target marker for insert_after, old text for replace/delete
        edit_content: New content for the edit
        edit_reason: Why this edit is proposed
        skip_validation: If True, skip validation gate (for manual edits)

    Returns:
        Dict with success, new_version, applied_edits, rejected_edits, validation
    """
    from src.core.intelligence.skill_editor import (
        EditOp,
        SkillEdit,
        apply_edits,
    )
    from src.core.intelligence.skill_validator import validate_skill_candidate

    # Rate limiting: prevent rapid-fire edits to the same skill
    cooldown_key = (owner_id, skill_name.lower())
    now = datetime.now(UTC)

    async with _cooldown_lock:
        # TTL eviction: remove stale entries to prevent unbounded growth
        stale_keys = [
            k
            for k, ts in _edit_cooldowns.items()
            if (now - ts).total_seconds() > _COOLDOWN_TTL_SECONDS
        ]
        for k in stale_keys:
            del _edit_cooldowns[k]

        last_edit = _edit_cooldowns.get(cooldown_key)
        if last_edit is not None:
            elapsed = (now - last_edit).total_seconds()
            cooldown_sec = settings.skill_edit_cooldown_sec
            if elapsed < cooldown_sec:
                remaining = int(cooldown_sec - elapsed)
                return {
                    "success": False,
                    "error": (
                        f"Rate limited: wait {remaining}s before editing "
                        f"{skill_name!r} again"
                    ),
                    "cooldown_remaining": remaining,
                }
        _edit_cooldowns[cooldown_key] = now

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        skill = await get_skill_by_name(session, owner, skill_name)
        if skill is None:
            return {"success": False, "error": f"Skill {skill_name!r} not found"}

        # Build the edit
        try:
            op = EditOp(edit_op)
        except ValueError:
            return {"success": False, "error": f"Invalid edit operation: {edit_op!r}"}

        edit = SkillEdit(
            op=op,
            target=edit_target,
            content=edit_content,
            reason=edit_reason,
        )

        # Apply bounded edits
        result = apply_edits(
            skill.body,
            [edit],
            edit_budget=settings.skill_edit_budget,
            current_version=skill.version or "1.0.0",
        )

        if not result.success:
            # Save to rejected-edits buffer
            rejected = skill.rejected_edits_json or []
            for rejected_edit, reason in result.rejected_edits:
                rejected.append(
                    {
                        **rejected_edit.to_dict(),
                        "reason": reason,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
            skill.rejected_edits_json = rejected[-MAX_REJECTED_EDITS:]

            await session.flush()
            return {
                "success": False,
                "error": "Edit could not be applied",
                "rejected_edits": [
                    {"edit": e.to_dict(), "reason": r} for e, r in result.rejected_edits
                ],
            }

        # Validation gate (unless skipped for manual edits)
        validation_passed = True
        validation_summary = ""

        if not skip_validation and settings.skill_validation_enabled:
            validation = await validate_skill_candidate(
                owner_id,
                skill_name,
                result.new_body,
                is_edit=True,
                original_body=skill.body,
            )
            validation_passed = validation.accepted
            validation_summary = validation.summary

            if not validation_passed:
                # Save to rejected-edits buffer
                rejected = skill.rejected_edits_json or []
                rejected.append(
                    {
                        **edit.to_dict(),
                        "reason": f"Validation failed: {validation.reason}",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
                skill.rejected_edits_json = rejected[-MAX_REJECTED_EDITS:]
                await session.flush()

                return {
                    "success": False,
                    "error": "Validation gate rejected the edit",
                    "validation": validation_summary,
                    "rejected_edits": [
                        {"edit": edit.to_dict(), "reason": validation.reason}
                    ],
                }

            skill.validation_score = validation.score_after

            # Auto-rollback if score dropped below threshold
            if validation.score_after < 0.3:
                if skill.best_body:
                    # Score is critically low — rollback to best_body
                    skill.body = skill.best_body
                    skill.validation_score = None  # Will be recalculated
                    logger.warning(
                        "auto-rollback: score %.2f < 0.3 for %r, reverting to best_body",
                        validation.score_after,
                        skill_name,
                    )
                    # Still record the edit in history as "auto-rollback"
                    history = skill.edit_history_json or []
                    history.append(
                        {
                            "op": "auto-rollback",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "reason": (
                                f"Score dropped to {validation.score_after:.2f} "
                                f"(< 0.3 threshold)"
                            ),
                        }
                    )
                    skill.edit_history_json = history[-20:]
                    await session.flush()
                    return {
                        "success": False,
                        "error": "Auto-rollback: score dropped below threshold",
                        "auto_rolled_back": True,
                        "validation": validation_summary,
                    }

                # No safe baseline to roll back to — reject the edit outright
                rejected = skill.rejected_edits_json or []
                rejected.append(
                    {
                        **edit.to_dict(),
                        "reason": (
                            f"Score dropped to {validation.score_after:.2f} "
                            f"(< 0.3 threshold) and no best_body baseline exists"
                        ),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
                skill.rejected_edits_json = rejected[-MAX_REJECTED_EDITS:]
                await session.flush()
                return {
                    "success": False,
                    "error": (
                        "Auto-rollback: score dropped below threshold and no "
                        "best_body baseline exists"
                    ),
                    "auto_rolled_back": False,
                    "validation": validation_summary,
                    "rejected_edits": [
                        {
                            "edit": edit.to_dict(),
                            "reason": "Score too low, no baseline",
                        }
                    ],
                }

            # Only update best_body AFTER rollback check (to avoid overwriting
            # best_body with a low-score improvement that would then be rolled back)
            if validation.score_delta > 0 and validation.score_after >= 0.3:
                skill.best_body = result.new_body

        # Apply the edit
        old_version = skill.version or "1.0.0"
        from src.core.intelligence.skill_editor import bump_version

        new_version = bump_version(old_version, result.version_bump)

        # Update edit history
        history = skill.edit_history_json or []
        for applied_edit in result.applied_edits:
            history.append(
                {
                    **applied_edit.to_dict(),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "version_before": old_version,
                    "version_after": new_version,
                }
            )
        skill.edit_history_json = history[-20:]  # Keep last 20 edits

        # Apply changes
        skill.body = result.new_body
        skill.version = new_version
        skill.updated_at = datetime.now(UTC)
        await session.flush()

        # Prepare success result while we still have access to result.applied_edits
        success_result = {
            "success": True,
            "new_version": new_version,
            "applied_edits": [e.to_dict() for e in result.applied_edits],
            "rejected_edits": [
                {"edit": e.to_dict(), "reason": r} for e, r in result.rejected_edits
            ],
            "validation": validation_summary,
        }

    # Session committed — cache invalidation happens AFTER commit
    from src.core.context_cache import invalidate as cache_invalidate

    try:
        await cache_invalidate(f"skills:{owner_id}:")
    except Exception:
        logger.exception(
            "Skill edit persisted for owner %d but cache invalidation failed; "
            "cache may be stale until TTL expires.",
            owner_id,
        )

    return success_result


async def promote_to_global(
    owner_id: int,
    skill_name: str,
    *,
    session: AsyncSession | None = None,
) -> bool:
    """Copy a user-scoped skill to global scope (``user_id=0``).

    A global skill is available to all users.  Only the original owner
    can promote; the original skill remains unchanged.

    If a global skill with the same name already exists, promotion is
    skipped.

    Args:
        owner_id: Owner Telegram user ID.
        skill_name: Name of the skill to promote.
        session: Optional existing session. If provided, the caller owns the
            transaction and is responsible for committing it.

    Returns:
        True if the skill was promoted, False if not found or already global.
    """
    async with _curator_session(session) as session:
        owner = await get_or_create_user(session, owner_id)
        skill = await get_skill_by_name(session, owner, skill_name)
        if skill is None:
            logger.warning(
                "curator: promote_to_global %r not found for %d",
                skill_name,
                owner_id,
            )
            return False

        if skill.review_status != "approved":
            logger.warning(
                "curator: promote_to_global %r rejected — status=%r (not approved)",
                skill_name,
                skill.review_status,
            )
            return False

        # Check if a global variant already exists
        existing = (
            await session.execute(
                select(Skill).where(
                    Skill.user_id == 0,
                    func.lower(Skill.name) == skill_name.lower().strip(),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info(
                "curator: global skill %r already exists, skipping promote",
                skill_name,
            )
            return False

        global_skill = Skill(
            user_id=0,
            name=skill.name,
            description=skill.description,
            trigger_patterns_json=skill.trigger_patterns_json,
            body=skill.body,
            enabled=True,
            review_status="approved",
        )
        session.add(global_skill)
        await session.flush()

    logger.info(
        "curator: promoted skill %r from user %d to global",
        skill_name,
        owner_id,
    )
    return True


async def curator_stats(owner_id: int) -> dict[str, int]:
    """Return curator statistics for the given owner.

    Returns a dict with keys:
        proposed  — count of proposed skills
        approved  — count of approved skills
        rejected  — count of rejected skills
        global    — count of global (user_id=0) skills
        total     — sum of proposed + approved + rejected
    """
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)

        q = select(
            func.sum(case((Skill.review_status == "proposed", 1), else_=0)).label(
                "proposed"
            ),
            func.sum(case((Skill.review_status == "approved", 1), else_=0)).label(
                "approved"
            ),
            func.sum(case((Skill.review_status == "rejected", 1), else_=0)).label(
                "rejected"
            ),
        ).where(Skill.user_id == owner.id)

        row = (await session.execute(q)).one()
        proposed_cnt = int(row.proposed or 0)
        approved_cnt = int(row.approved or 0)
        rejected_cnt = int(row.rejected or 0)

        global_cnt = (
            await session.execute(
                select(func.count(Skill.id)).where(Skill.user_id == 0)
            )
        ).scalar() or 0

    return {
        "proposed": proposed_cnt,
        "approved": approved_cnt,
        "rejected": rejected_cnt,
        "global": global_cnt,
        "total": proposed_cnt + approved_cnt + rejected_cnt,
    }


async def decay_stale_skills(session: AsyncSession, telegram_id: int) -> int:
    """Авто-отключение навыков с упавшим success_rate.

    Критерии отключения:
    - enabled=True
    - usage_count >= 10 (достаточно данных)
    - success_rate < 0.3 (только 30% использований успешны)

    Возвращает количество отключённых навыков.
    """
    owner = await get_or_create_user(session, telegram_id)
    # push usage_count >= 10 filter to SQL, keep only success_rate check in Python
    result = await session.execute(
        select(Skill).where(
            Skill.user_id == owner.id,
            Skill.enabled.is_(True),
            (
                func.coalesce(Skill.success_count, 0)
                + func.coalesce(Skill.failure_count, 0)
            )
            >= 10,
        )
    )
    skills = result.scalars().all()

    decayed = 0
    for skill in skills:
        usage_count = (skill.success_count or 0) + (skill.failure_count or 0)
        success_rate = (skill.success_count or 0) / max(usage_count, 1)
        if success_rate < 0.3:
            skill.enabled = False
            skill.disabled_at = datetime.now(UTC)
            old_desc = skill.description or ""
            skill.description = (
                old_desc + f" [DECAYED: success_rate={success_rate:.0%}]"
            )
            logger.info(
                "Skill %r decayed: success_rate=%.0f%% (%d/%d)",
                skill.name,
                success_rate * 100,
                skill.success_count or 0,
                usage_count,
            )
            decayed += 1

    if decayed:
        await session.flush()

    return decayed


async def archive_long_disabled(
    session: AsyncSession, telegram_id: int, *, days: int = 90
) -> int:
    """Archive skills that have been disabled for more than ``days``.

    Archived skills get ``review_status="archived"`` and are no longer
    considered by the curator loops.
    """
    if days <= 0:
        logger.warning(
            "archive_long_disabled: days=%d is invalid, must be > 0. Skipping.",
            days,
        )
        return 0

    owner = await get_or_create_user(session, telegram_id)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        select(Skill).where(
            Skill.user_id == owner.id,
            Skill.enabled.is_(False),
            Skill.disabled_at.isnot(None),
            Skill.disabled_at < cutoff,
            Skill.review_status != "archived",
        )
    )
    archived = 0
    for skill in result.scalars().all():
        skill.review_status = "archived"
        skill.updated_at = datetime.now(UTC)
        old_desc = skill.description or ""
        skill.description = old_desc + f" [ARCHIVED: disabled >{days}d]"
        logger.info("Skill %r archived after %d days disabled", skill.name, days)
        archived += 1

    if archived:
        await session.flush()

    return archived


# ── background loop ──────────────────────────────────────────────────


async def curator_loop(owner_telegram_id: int) -> None:
    """Background loop: every 6 hours run auto-approval + suggestions + rollback.

    Runs:
        1. ``auto_approve_high_confidence()``
        2. ``suggest_skills_from_trajectories(owner_telegram_id)``
        3. ``propose_skills_from_analysis(owner_telegram_id)``
        4. ``rollback_all_regressed()`` — V3: auto-rollback regressed skills
    """
    from src.core.intelligence.skill_compact_optimizer import rollback_all_regressed
    from src.core.intelligence.skills import (
        propose_skills_from_analysis,
        suggest_skills_from_trajectories,
    )

    interval_sec = 6 * 3600  # 6 hours

    try:
        while True:
            async with _overlap_guard:
                try:
                    approved = await auto_approve_high_confidence(owner_telegram_id)
                    if approved:
                        logger.info("curator_loop: auto-approved %d skills", approved)
                except Exception:
                    logger.exception(
                        "curator_loop: auto_approve_high_confidence failed"
                    )

                try:
                    suggested = await suggest_skills_from_trajectories(
                        owner_telegram_id
                    )
                    if suggested:
                        try:
                            await notification_queue.enqueue(
                                topic="skills",
                                category="curator",
                                priority=2,
                                text=(
                                    f"🧠 Куратор предложил {suggested} "
                                    f"новый навык(ов) из недавних запросов."
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "curator_loop: notification enqueue failed after suggest"
                            )
                except Exception:
                    logger.exception(
                        "curator_loop: suggest_skills_from_trajectories failed"
                    )

                try:
                    proposed = await propose_skills_from_analysis(owner_telegram_id)
                    if proposed:
                        names = [s["name"] for s in proposed]
                        try:
                            await notification_queue.enqueue(
                                topic="skills",
                                category="curator",
                                priority=2,
                                text=(
                                    f"🧠 Curator proposed {len(proposed)} skill(s) "
                                    f"from analysis: {', '.join(names[:5])}."
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "curator_loop: notification enqueue failed after propose"
                            )
                except Exception:
                    logger.exception(
                        "curator_loop: propose_skills_from_analysis failed"
                    )

                # V3: Auto-rollback regressed skills (0 токенов)
                try:
                    rolled_back = await rollback_all_regressed(owner_telegram_id)
                    if rolled_back:
                        try:
                            await notification_queue.enqueue(
                                topic="skills",
                                category="curator",
                                priority=3,
                                text=(
                                    f"♻️ Curator auto-rolled back {rolled_back} regressed skill(s). "
                                    f"Check /skills for details."
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "curator_loop: notification enqueue failed after rollback"
                            )
                except Exception:
                    logger.exception("curator_loop: rollback_all_regressed failed")

                # Health decay: отключаем мёртвые навыки
                try:
                    async with get_session() as session:
                        decayed = await decay_stale_skills(session, owner_telegram_id)
                    if decayed:
                        logger.info(
                            "Decayed %d stale skills for user %d",
                            decayed,
                            owner_telegram_id,
                        )
                except Exception:
                    logger.exception("curator_loop: decay_stale_skills failed")

                # Archive skills disabled long ago (cleanup after decay)
                try:
                    async with get_session() as session:
                        archived = await archive_long_disabled(
                            session, owner_telegram_id
                        )
                    if archived:
                        logger.info(
                            "Archived %d long-disabled skills for user %d",
                            archived,
                            owner_telegram_id,
                        )
                except Exception:
                    logger.exception("curator_loop: archive_long_disabled failed")

            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        logger.info("curator_loop: cancelled, shutting down")
        raise


# ── task registration ────────────────────────────────────────────────

task_manager.register(
    "skill-curator",
    partial(curator_loop, settings.owner_telegram_id),
)
