"""MemOS-inspired reward-weighted self-evolving memory loop.

Rides existing loops (dream_cycle, skill_optimizer_loop, auto_evolve_loop).
All functions are called from existing loops — registers no task of its own.
Gated by settings.reward_loop_enabled (default ON).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
from datetime import datetime, timezone

from sqlalchemy import select, update, func, case

from src.config import settings
from src.core.infra.key_guard import mask_keys, mask_pii

# ponytail: Semaphore(2) limits concurrent LLM rubric calls — prevents API key exhaustion
_rubric_semaphore = asyncio.Semaphore(2)
from src.core.intelligence.skill_validator import _estimate_skill_quality_heuristic
from src.db.models._learning import Trajectory, Skill
from src.db.session import get_session

logger = logging.getLogger(__name__)


def _validate_reward(v: float) -> float:
    """Trust boundary: NaN/inf → 0.0, clamp [-1, 1]. C4 safety."""
    if not math.isfinite(v):
        return 0.0
    return max(-1.0, min(1.0, v))


async def compute_reward(
    *,
    success: bool,
    latency_ms: int | None,
    response_text: str | None,
    used_skills_json: list | None,
    route_mode: str | None,
    corrected_by_user: bool = False,
) -> tuple[float, str]:
    """Compute R ∈ [-1, 1] + reflection text.

    Heuristic-first (always available). LLM rubric opt-in via settings.reward_llm_rubric_enabled.
    """
    # Heuristic reward (pure, no LLM, cannot fail)
    r = 0.0
    if success:
        r += 0.5
    else:
        r -= 0.5

    if latency_ms is not None:
        # Guard: negative/zero latency is invalid data, treat as missing
        if latency_ms > 0:
            if latency_ms < 2000:
                r += 0.2
            elif latency_ms < 5000:
                r += 0.1
            else:
                r -= 0.1

    if corrected_by_user:
        r -= 0.3  # user correction = negative signal

    if used_skills_json and len(used_skills_json) > 0:
        r += 0.1  # skill usage = positive signal

    r = _validate_reward(r)
    r_heuristic = r  # save for blending

    # LLM rubric (opt-in, OFF-PATH, timeout 30s)
    # ponytail: adaptive sampling — only call LLM when heuristic is uncertain (|r|<0.3).
    # At ±0.7+ the heuristic is decisive. Saves ~80% LLM calls, 0 overhead.
    if settings.reward_llm_rubric_enabled and abs(r_heuristic) < 0.3:
        try:
            r_llm = await _rubric_llm(success, latency_ms, response_text, route_mode)
            if r_llm is not None:
                # ponytail: blend 60% LLM + 40% heuristic — preserves corrected_by_user/skills signals
                r = _validate_reward(0.6 * _validate_reward(r_llm) + 0.4 * r_heuristic)
        except Exception:
            logger.debug("Reward rubric LLM failed, using heuristic", exc_info=True)

    reflection = f"success={success} latency={latency_ms}ms r={r:.3f}"
    return r, reflection


async def _rubric_llm(
    success: bool,
    latency_ms: int | None,
    response_text: str | None,
    route_mode: str | None,
) -> float | None:
    """3-axis rubric (goal/process/satisfaction) via build_provider. Returns None on any failure."""
    from src.db.repos.session_repo import get_or_create_user
    from src.llm.router import build_provider
    from src.llm.base import ChatMessage, TaskType

    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)
        if owner is None:
            return None
        try:
            provider = await build_provider(
                session, owner, purpose="background", task_type=TaskType.BACKGROUND
            )
        except Exception:
            return None
        if provider is None:
            return None
        # Detach owner — provider holds keys/endpoints as strings, no DB refs needed.
        # Prevents DetachedInstanceError if any lazy-load is triggered post-close.
        session.expunge(owner)

    # Security: scan response_text for prompt injection before sending to LLM
    from src.core.security.prompt_injection_scanner import scan_content

    _safe_response = mask_pii(mask_keys((response_text or "")[:500]))
    _scan = scan_content(_safe_response, "rubric_input")
    if _scan.blocked:
        logger.warning(
            "rubric LLM input blocked by injection scanner: %s", _scan.category
        )
        return None

    prompt = f"""Rate this AI response on 3 axes (0.0 to 1.0 each). Return JSON only.
Goal achieved: {"yes" if success else "no"}
Latency: {latency_ms}ms
Response: {_safe_response}
Route: {route_mode or "default"}

Return: {{"goal": 0.0, "process": 0.0, "satisfaction": 0.0}}"""

    try:
        async with _rubric_semaphore:
            result = await asyncio.wait_for(
                provider.chat(
                    [ChatMessage(role="user", content=prompt)],
                    task_type="reward_rubric",
                ),
                timeout=30.0,
            )
        scores = json.loads(result)
        r = (
            scores.get("goal", 0.5)
            + scores.get("process", 0.5)
            + scores.get("satisfaction", 0.5)
        ) / 3.0
        return r * 2.0 - 1.0  # normalize [0,1] → [-1,1]
    except Exception:
        return None


async def backprop_values(telegram_id: int, *, window: int = 200) -> int:
    """TD backprop: V_t = α_t·R_t + (1-α_t)·γ·V_{t+1}.

    Fetches window trajectories ordered by created_at DESC (uses ix_traj_user_created index).
    One Python recurrence pass → bulk UPDATE.
    OFF-PATH (called from dream_cycle). Returns count updated.

    Args:
        telegram_id: Owner's Telegram ID (resolved to DB user ID internally).
    """
    from src.db.repo import get_or_create_user

    gamma = settings.reward_gamma
    alpha_base = settings.reward_alpha_base

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        if owner is None:
            return 0
        owner_id = owner.id

        # Fetch window ordered DESC (most recent first)
        stmt = (
            select(Trajectory)
            .where(Trajectory.user_id == owner_id)
            .where(Trajectory.reward_value.isnot(None))
            .order_by(Trajectory.created_at.desc())
            .limit(window)
        )
        result = await session.execute(stmt)
        trajectories = list(result.scalars().all())

        if not trajectories:
            return 0

        # trajectories is DESC (newest first) — iterate directly for backprop:
        # newest → oldest = terminal → root, v_next starts at 0 (terminal V=0).
        # TD backprop: V_t = α_t·R_t + (1-α_t)·γ·V_{t+1}
        values: list[tuple[float, int]] = []
        v_next = 0.0
        for t in trajectories:  # newest first (terminal backward)
            r = _validate_reward(t.reward_value if t.reward_value is not None else 0.0)
            # Guard: step_index is non-nullable int (default 0), but max(0, ...)
            # prevents ZeroDivisionError if a negative value slips through.
            step = max(0, t.step_index)
            # ponytail: step=0 gives α=alpha_base; step→∞ gives α→0. Clamp for safety.
            alpha_t = alpha_base / (1.0 + step)
            alpha_t = max(0.01, min(1.0, alpha_t))  # clamp
            v_t = alpha_t * r + (1.0 - alpha_t) * gamma * v_next
            v_t = _validate_reward(v_t)
            values.append((v_t, t.id))
            v_next = v_t

        # Bulk UPDATE via single CASE expression (not N individual writes)
        if values:
            ids = [tid for _, tid in values]
            case_expr = case(
                {tid: v for v, tid in values},
                value=Trajectory.id,
                else_=Trajectory.value_estimate,
            )
            await session.execute(
                update(Trajectory)
                .where(Trajectory.id.in_(ids))
                .values(value_estimate=case_expr)
            )
            await session.commit()

        return len(values)


async def induce_policies(telegram_id: int) -> int:
    """L2 policy induction: group trajectories by signature, require ≥N episodes,
    compute gain, insert as Skill(review_status='pending', policy_signature=...).

    Called from skill_optimizer_loop Step 4. OFF-PATH.

    Args:
        telegram_id: Owner's Telegram ID (resolved to DB user ID internally).
    """
    from src.db.repo import get_or_create_user

    min_episodes = settings.reward_min_episodes

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        if owner is None:
            return 0
        owner_id = owner.id

        # Group by route_mode + success/failure signature
        stmt = (
            select(
                Trajectory.route_mode,
                Trajectory.success,
                func.count(Trajectory.id).label("cnt"),
                func.avg(Trajectory.value_estimate).label("avg_v"),
            )
            .where(Trajectory.user_id == owner_id)
            .where(Trajectory.value_estimate.isnot(None))
            .group_by(Trajectory.route_mode, Trajectory.success)
        )
        result = await session.execute(stmt)
        groups = result.all()

        induced = 0
        for row in groups:
            if row.cnt < min_episodes:
                continue

            signature = (
                f"{row.route_mode or 'default'}|{'success' if row.success else 'fail'}"
            )
            avg_v = float(row.avg_v or 0.0)

            # Check if policy already exists (limit(1): safe against duplicates —
            # no unique constraint on (user_id, policy_signature))
            existing = await session.execute(
                select(Skill)
                .where(
                    Skill.policy_signature == signature,
                    Skill.user_id == owner_id,
                )
                .limit(1)
            )
            if existing.scalar_one_or_none():
                continue

            # Create candidate policy as pending Skill
            skill = Skill(
                user_id=owner_id,
                name=f"policy_{signature}",
                body=(
                    f"# Auto-induced policy for {signature}\n"
                    f"# Avg value: {avg_v:.3f}\n"
                    f"# Episodes: {row.cnt}"
                ),
                review_status="pending",
                policy_signature=signature,
                induction_gain=avg_v,
                episode_count=int(row.cnt),
                eta_alpha=1.0,
                eta_beta=1.0,
            )
            session.add(skill)
            induced += 1

        if induced:
            await session.commit()

        return induced


async def update_world_model(telegram_id: int) -> dict:
    """L3 world-model abstraction → abstract to (ℰ,ℐ,C) triples
    via build_provider → upsert into Qdrant with payload_type='world_model'.

    Called from dream_cycle Phase 15. OFF-PATH.

    Args:
        telegram_id: Owner's Telegram ID (resolved to DB user ID internally).
    """
    from src.db.repos.session_repo import get_or_create_user
    from src.llm.router import build_provider
    from src.llm.base import ChatMessage, TaskType
    from src.core.actions.embedding_cache import aget as embed_get

    # Phase 1: DB session — fetch data, then CLOSE session before LLM call (don't hold 60s)
    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, telegram_id)
            if owner is None:
                return {"error": "no_owner"}

            provider = await build_provider(
                session, owner, purpose="background", task_type=TaskType.BACKGROUND
            )
            if provider is None:
                return {"error": "no_provider"}

            # Abstract recent high-value trajectories
            stmt = (
                select(Trajectory)
                .where(Trajectory.user_id == owner.id)
                .where(Trajectory.value_estimate.isnot(None))
                .order_by(Trajectory.value_estimate.desc())
                .limit(10)
            )
            result = await session.execute(stmt)
            top_trajectories = list(result.scalars().all())
            # Detach data from session before closing — expunge to avoid lazy-load crashes
            for t in top_trajectories:
                session.expunge(t)
            session.expunge(owner)  # provider holds keys, no DB refs needed post-close
            owner_id_resolved = owner.id
        # Session CLOSED here — DB connection released

        if not top_trajectories:
            return {"abstracted": 0}

        # Phase 2: LLM call OUTSIDE session — no DB connection held during 60s timeout
        # Security: scan each trajectory text for prompt injection before LLM
        from src.core.security.prompt_injection_scanner import scan_content

        summaries = []
        for t in top_trajectories:
            if t is None:
                continue
            _req = mask_pii(mask_keys((t.request_text or "")[:200]))
            _resp = mask_pii(mask_keys((t.response_text or "")[:200]))
            # Skip trajectories with injection patterns (defence-in-depth)
            if (
                scan_content(_req, "world_model_input").blocked
                or scan_content(_resp, "world_model_input").blocked
            ):
                logger.warning(
                    "world_model: trajectory %s blocked by injection scanner", t.id
                )
                continue
            summaries.append(f"- {_req} → {_resp} (V={(t.value_estimate or 0.0):.2f})")

        if not summaries:
            return {"abstracted": 0}
        prompt = f"""Abstract these interactions into a world model.
Identify: ℰ (environment effects), ℐ (interventions that work), C (constraints).
Return JSON: {{"effects": [...], "interventions": [...], "constraints": [...]}}

Interactions:
{chr(10).join(summaries)}"""

        try:
            result_text = await asyncio.wait_for(
                provider.chat(
                    [ChatMessage(role="user", content=prompt)],
                    task_type="world_model",
                ),
                timeout=60.0,
            )
            abstraction = json.loads(result_text)
        except Exception:
            logger.debug("World model LLM failed", exc_info=True)
            return {"abstracted": 0}

        # Phase 3: Qdrant upsert — no DB session needed
        qdrant_ok = False
        try:
            from src.core.actions.vector_store import get_vector_store

            vs = await get_vector_store()
            abstraction_text = json.dumps(abstraction, ensure_ascii=False)
            # Truncate BEFORE embedding so vector and stored text match
            fact_text = abstraction_text[:2000]
            embedding = await embed_get(fact_text, "default")
            if embedding is not None:
                await vs.upsert_memory(
                    # ponytail: hash → 31-bit positive int for Qdrant int payload; token_hex prevents collision
                    memory_id=hash(
                        f"wm_{owner_id_resolved}_{datetime.now(timezone.utc).isoformat()}"
                        f"_{secrets.token_hex(4)}"
                    )
                    & 0x7FFFFFFF,
                    user_id=owner_id_resolved,
                    contact_id=None,
                    fact=fact_text,
                    embedding=embedding,
                    importance=0.7,
                    confidence=0.5,
                    payload_type="world_model",
                )
                qdrant_ok = True
        except Exception:
            logger.debug("World model Qdrant upsert failed", exc_info=True)

        return {
            "abstracted": 1,
            "abstraction": abstraction,
            "qdrant_ok": qdrant_ok,
        }

    except Exception:
        logger.debug("Unexpected error in update_world_model", exc_info=True)
        return {"error": "unexpected"}


async def crystallize_policies(telegram_id: int) -> dict:
    """Crystallize L2 policies (pending Skills) with high η into active skills.

    η ~ Beta(eta_alpha, eta_beta) on Skill. Crystallize when mean ≥ 0.5 AND episode_count ≥ N.
    Flips pending→approved or pending→rejected.

    Called from auto_evolve_loop. OFF-PATH.

    Args:
        telegram_id: Owner's Telegram ID (resolved to DB user ID internally).
    """
    from src.db.repo import get_or_create_user

    min_episodes = settings.reward_min_episodes

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        if owner is None:
            return {"crystallized": 0, "rejected": 0}
        owner_id = owner.id

        # Find pending policies with enough episodes
        stmt = (
            select(Skill)
            .where(Skill.user_id == owner_id)
            .where(Skill.review_status == "pending")
            .where(Skill.policy_signature.isnot(None))
            .where(Skill.episode_count >= min_episodes)
        )
        result = await session.execute(stmt)
        candidates = list(result.scalars().all())

        crystallized = 0
        rejected = 0
        approve_ids: list[int] = []
        reject_ids: list[int] = []

        for skill in candidates:
            # Beta posterior mean
            alpha = skill.eta_alpha or 1.0
            beta = skill.eta_beta or 1.0
            # ponytail: Beta mean = α/(α+β), stable for large values
            eta_mean = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.5

            # Require at least one posterior update beyond pure prior
            # Beta(1,1) prior → total_evidence == 2.0. Allow fresh policies
            # through: induction_gain serves as initial quality signal.
            total_evidence = alpha + beta
            if total_evidence < 2.0:
                # Evidence below prior — should not happen; keep pending, skip
                continue

            # Reject policies with negative induction gain (avg value < 0)
            if (skill.induction_gain or 0.0) < 0.0:
                reject_ids.append(skill.id)
                rejected += 1
                continue

            # Heuristic quality gate — reject policies with empty/near-empty bodies
            # (auto-induced bodies are ~74 chars → score ~0.3; threshold 0.2 allows them)
            heuristic_score = _estimate_skill_quality_heuristic(
                skill.body or "", [], skill_name=skill.name
            )
            if heuristic_score < 0.20:
                reject_ids.append(skill.id)
                rejected += 1
                continue

            if eta_mean >= 0.5:
                approve_ids.append(skill.id)
                crystallized += 1
            else:
                reject_ids.append(skill.id)
                rejected += 1

        # Bulk UPDATE via single CASE expression (not N individual writes)
        if approve_ids:
            await session.execute(
                update(Skill)
                .where(Skill.id.in_(approve_ids))
                .values(review_status="approved")
            )
        if reject_ids:
            await session.execute(
                update(Skill)
                .where(Skill.id.in_(reject_ids))
                .values(review_status="rejected")
            )
        if crystallized or rejected:
            await session.commit()

        return {"crystallized": crystallized, "rejected": rejected}


async def update_eta_posterior(skill: Skill, *, success: bool) -> None:
    """Beta(eta_alpha, eta_beta) posterior update from SkillUsage outcome.

    success=True → eta_alpha += 1
    success=False → eta_beta += 1
    DB persist FIRST — crash-safe: if DB fails, memory stays consistent.
    Called after apply_skill_edit in auto_evolve.evolve_skill.
    """
    if success:
        new_alpha = (skill.eta_alpha or 1.0) + 1.0
        new_beta = skill.eta_beta or 1.0
    else:
        new_alpha = skill.eta_alpha or 1.0
        new_beta = (skill.eta_beta or 1.0) + 1.0

    # DB persist FIRST — crash-safe
    if hasattr(skill, "id") and skill.id is not None:
        try:
            async with get_session() as session:
                await session.execute(
                    update(Skill)
                    .where(Skill.id == skill.id)
                    .values(eta_alpha=new_alpha, eta_beta=new_beta)
                )
                await session.commit()
        except Exception:
            logger.warning(
                "update_eta_posterior: DB persist failed — memory unchanged, skill %s",
                getattr(skill, "id", None),
                exc_info=True,
            )
            return  # ← EARLY RETURN: memory stays consistent with DB

    # Only update in-memory AFTER successful DB commit (or skip for mocks)
    skill.eta_alpha = new_alpha
    skill.eta_beta = new_beta
