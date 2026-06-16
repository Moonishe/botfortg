"""Data-access helpers for the skills inline panel.

Extracted from ``skills_callbacks.py`` to keep the callback module under 400 lines.
Pure data-access layer — no router registration, no callback decorators.
Imports presentation helpers from ``skills_ui`` for status filtering in Python.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select

from src.core.context_cache import invalidate as cache_invalidate
from src.core.intelligence.skill_editor import bump_version
from src.db.models import Skill
from src.db.repo import list_skills
from .skills_ui import (
    _PAGE_SIZE,
    _is_stale,
)

# ponytail: CallbackQuery-typed helpers (_parse_callback_skill_id and
# _skill_mutation) live in skills_callbacks.py to keep this module free
# of aiogram imports.

logger = logging.getLogger(__name__)


# ── Data-access helpers ──────────────────────────────────────────────


def _status_filter_clauses(status: str, owner) -> list:
    """Return SQLAlchemy WHERE clauses for a given UI status.

    ``stale`` and ``archived`` are UI labels — the SQL for both is identical
    (disabled approved skills).  The split between stale/archived is done in
    Python via ``_is_stale()`` to avoid a LIKE scan on ``Skill.description``.
    """
    base = [Skill.user_id == owner.id]
    if status == "all":
        return base
    if status == "proposed":
        return [*base, Skill.review_status == "proposed"]
    if status == "active":
        return [
            *base,
            Skill.review_status == "approved",
            Skill.enabled == True,  # noqa: E712,
        ]
    if status == "rejected":
        return [*base, Skill.review_status == "rejected"]

    # stale/archived: both return the same disabled-clauses; split in Python
    if status in ("stale", "archived"):
        return [*base, Skill.review_status == "approved", Skill.enabled == False]  # noqa: E712
    return []


async def _count_skills(session, status: str, owner) -> int:
    """Return total count of skills for a UI status.

    Uses ``func.count()`` for all statuses.  The ``stale``/``archived``
    split is handled by ``_fetch_skills_by_status`` via Python filtering
    (no LIKE scan on ``Skill.description``).
    """
    # ponytail: stale/archived counts are intentionally handled by
    # _fetch_skills_by_status via Python filtering; this function does NOT
    # return accurate per-tab counts for those statuses.
    if status in ("stale", "archived"):
        raise ValueError(
            f"_count_skills does not support status={status!r}; "
            "use _fetch_skills_by_status for stale/archived counts"
        )
    clauses = _status_filter_clauses(status, owner)
    if not clauses:
        return 0
    result = await session.execute(select(func.count()).where(*clauses))
    return result.scalar() or 0


async def _fetch_skills_by_status(
    session, owner, status: str, page: int
) -> tuple[list[Skill], int]:
    """Fetch skills for a given UI status with server-side pagination.

    For ``stale``/``archived``, fetches disabled approved skills and filters
    in Python (no LIKE scan).  Returns (page_skills, total_count).
    """
    page = max(0, page)
    offset = page * _PAGE_SIZE

    if status in ("stale", "archived"):
        # ponytail: Python filtering avoids LIKE scan.  Guard with a DB-side
        # limit so the rare case of thousands of disabled skills does not
        # balloon memory; correctness does not require exact pagination here.
        clauses = _status_filter_clauses(status, owner)
        q = (
            select(Skill)
            .where(*clauses)
            .order_by(Skill.success_count.desc(), Skill.updated_at.desc())
            .limit(200)
        )
        r = await session.execute(q)
        all_skills = list(r.scalars().all())

        if status == "stale":
            filtered = [s for s in all_skills if _is_stale(s)]
        else:
            filtered = [s for s in all_skills if not _is_stale(s)]

        total = len(filtered)
        page_skills = filtered[offset : offset + _PAGE_SIZE]
        return page_skills, total

    total = await _count_skills(session, status, owner)

    if status == "all":
        skills = await list_skills(session, owner, limit=_PAGE_SIZE, offset=offset)
    elif status == "proposed":
        skills = await list_skills(
            session, owner, review_status="proposed", limit=_PAGE_SIZE, offset=offset
        )
    elif status == "active":
        skills = await list_skills(
            session,
            owner,
            review_status="approved",
            enabled=True,
            limit=_PAGE_SIZE,
            offset=offset,
        )
    elif status == "rejected":
        skills = await list_skills(
            session,
            owner,
            review_status="rejected",
            limit=_PAGE_SIZE,
            offset=offset,
        )
    else:
        skills = []

    return skills, total


async def _get_skill_by_id(session, owner, skill_id: int):
    """Fetch a skill by ID scoped to the owner."""
    skill = await session.get(Skill, skill_id)
    if skill is None or skill.user_id != owner.id:
        return None
    return skill


async def _perform_rollback(session, skill, owner, reason: str) -> None:
    """Mutate a skill to its best_body and record the rollback.

    Caller must ensure ``skill.best_body`` is not None.
    """
    old_version = skill.version or "1.0.0"
    skill.body = skill.best_body
    skill.validation_score = None
    skill.version = bump_version(old_version, "minor")
    history = list(skill.edit_history_json or [])
    history.append(
        {
            "op": "rollback",
            "timestamp": datetime.now(UTC).isoformat(),
            "reason": reason,
        }
    )
    # Cap history to match apply_skill_edit branches and prevent unbounded
    # growth on repeated rollbacks.
    skill.edit_history_json = history[-20:]
    await session.flush()
    try:
        await cache_invalidate(f"skills:{owner.telegram_id}:")
    except Exception:
        logger.warning(
            "skills cache invalidate failed for owner %s",
            owner.telegram_id,
            exc_info=True,
        )
