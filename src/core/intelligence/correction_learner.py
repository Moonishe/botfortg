"""Correction Learner — learns from user corrections to improve over time.

When the user corrects the bot ("нет, не так", "не, через два", "отмени"),
this module:
  1. Records the correction pattern in in-memory history
  2. Feeds it into the humanizer feedback loop (to improve future rewrites)
  3. Updates adaptive persona if it's a style correction
  4. Updates memory if it's a fact correction

Integration point: smart_correction stage 0d → learn_correction()
Context injection:  maestro pre-loads recent corrections → prompt_assembler injects.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.db.session import get_session
from src.db.repo import get_or_create_user, get_persona, update_persona

logger = logging.getLogger(__name__)

# ── In-memory correction history ──────────────────────────────────────
# Structure: {telegram_id: [(original_text, corrected_text, timestamp), ...]}
_correction_history: dict[int, list[tuple[str, str, float]]] = {}
_correction_lock = asyncio.Lock()
_MAX_HISTORY = 50  # max entries per user
# ponytail: evict users with no corrections for > 24 hours
_CORRECTION_USER_TTL = 86400
_correction_last_access: dict[int, float] = {}

# ── DB write serialization for step 5 (rejected_edits_json) ───────────
# Prevents lost-update race: two concurrent corrections for the same user
# both read the same Skill row → both append to rejected_edits_json →
# last commit wins, silently dropping the other update.
# Per-user locks: User A's correction does NOT block User B.
_db_write_locks: dict[int, asyncio.Lock] = {}
_db_write_locks_guard = asyncio.Lock()
_MAX_LOCK_ENTRIES = 100  # ponytail: evict idle entries when map grows too large
# ── Time-based eviction: only remove entries idle > _LOCK_IDLE_TTL seconds ──
_LOCK_IDLE_TTL = 600  # 10 min — if a user hasn't used their lock in this time, evict
_lock_last_used: dict[int, float] = {}  # per-user last-access timestamp


async def _get_db_write_lock(telegram_id: int) -> asyncio.Lock:
    """Get or create a per-user asyncio.Lock for DB write serialization."""
    now = time.monotonic()
    async with _db_write_locks_guard:
        # Evict ONLY idle entries — never evict a lock that another coroutine may hold.
        if len(_db_write_locks) > _MAX_LOCK_ENTRIES:
            stale = [
                uid
                for uid, last in _lock_last_used.items()
                if now - last > _LOCK_IDLE_TTL
            ]
            for uid in stale:
                _db_write_locks.pop(uid, None)
                _lock_last_used.pop(uid, None)
            if stale:
                logger.debug(
                    "Evicted %d idle db_write_locks (now %d total)",
                    len(stale),
                    len(_db_write_locks),
                )
        if telegram_id not in _db_write_locks:
            _db_write_locks[telegram_id] = asyncio.Lock()
        _lock_last_used[telegram_id] = now
        return _db_write_locks[telegram_id]


async def learn_correction(
    telegram_id: int,
    original_text: str,
    corrected_text: str,
    feedback_type: str = "rewrite",  # "rewrite" | "fact" | "style" | "cancel"
) -> None:
    """Record a correction for future learning.

    Args:
        telegram_id:  Telegram user ID.
        original_text: What the bot said/did (or the raw user command).
        corrected_text: What the user wanted instead.
        feedback_type:  Category of correction.
    """
    # ── 1. Store in in-memory history ──
    async with _correction_lock:
        now = time.monotonic()
        # Evict users inactive > 24h (memory leak prevention)
        stale_users = [
            uid
            for uid, last in _correction_last_access.items()
            if uid in _correction_history and now - last > _CORRECTION_USER_TTL
        ]
        for uid in stale_users:
            _correction_history.pop(uid, None)
            _correction_last_access.pop(uid, None)
        if telegram_id not in _correction_history:
            _correction_history[telegram_id] = []
        history = _correction_history[telegram_id]
        _correction_last_access[telegram_id] = now
        history.append(
            (
                original_text[:500],
                corrected_text[:500],
                asyncio.get_running_loop().time(),
            )
        )
        if len(history) > _MAX_HISTORY:
            history.pop(0)  # evict oldest

    logger.debug(
        "Correction learned: type=%s, user=%d, %d history entries",
        feedback_type,
        telegram_id,
        len(history),
    )

    # ── 2. Feed into humanizer feedback loop ──
    try:
        from src.core.humanizer.humanizer import record_humanizer_feedback

        record_humanizer_feedback(
            telegram_id,
            original=original_text,
            corrected=corrected_text,
            accepted=False,
        )
    except Exception:
        logger.debug(
            "Non-critical error", exc_info=True
        )  # humanizer feedback is optional

    # ── 3. Update adaptive persona if style correction ──
    if feedback_type == "style" and corrected_text:
        try:
            async with get_session() as session:
                owner = await get_or_create_user(session, telegram_id)
                persona = await get_persona(session, owner)

                from src.core.intelligence.adaptive_persona import (
                    detect_persona_change,
                )

                change = await detect_persona_change(corrected_text)
                if change and change.get("changes"):
                    await update_persona(
                        session,
                        persona,
                        **change["changes"],
                        auto=True,  # accepted by maintainability lint
                    )
                    logger.info(
                        "Persona updated from correction for user %d: %s",
                        telegram_id,
                        change.get("reason", "unknown"),
                    )
        except Exception:
            logger.debug("Persona update from correction failed", exc_info=True)

    # ── 4. If fact correction — queue memory update ──
    if feedback_type == "fact" and corrected_text:
        try:
            from src.core.memory.memory_queue import enqueue, MemoryJob

            await enqueue(
                MemoryJob(
                    telegram_id=telegram_id,
                    facts=[{"fact": corrected_text, "confidence": 0.9}],
                    job_type="save",
                )
            )
        except asyncio.QueueFull:
            logger.debug(
                "Memory queue full, fact correction dropped for user %d", telegram_id
            )
        except Exception:
            logger.debug("Memory update from correction failed", exc_info=True)

    # ── 5. Feed dissatisfaction into skill rejected_edits_json ──
    if feedback_type in ("rewrite", "fact") and original_text:
        try:
            from src.core.intelligence.skill_editor import (
                parse_nl_feedback,
                MAX_REJECTED_EDITS,
            )
            from src.db.models import Skill

            _write_lock = await _get_db_write_lock(telegram_id)
            async with _write_lock:
                async with get_session() as session:
                    owner = await get_or_create_user(session, telegram_id)
                    # Find last-used skill for this user
                    from sqlalchemy import select

                    stmt = (
                        select(Skill)
                        .where(Skill.user_id == owner.id, Skill.enabled.is_(True))
                        .order_by(Skill.last_used_at.desc().nullslast())
                        .limit(1)
                    )
                    result = await session.execute(stmt)
                    skill = result.scalar_one_or_none()
                    if skill:
                        rejected_entry = parse_nl_feedback(
                            feedback=corrected_text or original_text,
                            skill_name=skill.name,
                            target=original_text[:200],
                        )
                        if rejected_entry:
                            rejected = skill.rejected_edits_json or []
                            rejected.append(rejected_entry)
                            skill.rejected_edits_json = rejected[-MAX_REJECTED_EDITS:]
                            await session.commit()
                            logger.debug(
                                "Rejected edit recorded for skill '%s' (user %d)",
                                skill.name,
                                telegram_id,
                            )
                        else:
                            logger.info(
                                "Feedback blocked by injection scanner for user %d "
                                "(skill '%s') — feedback rejected for safety",
                                telegram_id,
                                skill.name,
                            )
        except Exception:
            logger.debug("rejected_edits_json update failed", exc_info=True)


async def get_recent_corrections(
    telegram_id: int,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Get recent corrections for context injection into system prompt.

    Returns:
        List of dicts with keys: original, corrected.
    """
    async with _correction_lock:
        _correction_last_access[telegram_id] = time.monotonic()
        history = _correction_history.get(telegram_id, [])
        return [
            {"original": orig, "corrected": corr} for orig, corr, _ in history[-limit:]
        ]


async def get_correction_stats(telegram_id: int) -> dict[str, int]:
    """Return correction statistics for /health or dashboards."""
    async with _correction_lock:
        return {
            "user_corrections": len(_correction_history.get(telegram_id, [])),
            "global_total": sum(len(v) for v in _correction_history.values()),
        }
