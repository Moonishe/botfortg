"""Compaction Pipeline v2 orchestrator — 7-phase nightly cycle.

Phase stub functions are async and return sensible defaults.
The real pruning/compression/learning logic will be implemented in later commits.
The orchestration layer (try/except aggregation, compression_ratio) is functional.
"""

from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import TYPE_CHECKING

from src.config import settings
from src.core.compaction.models import CompactionReport, CompressResult
from src.core.compaction.skills import extract_skills_from_trajectories
from src.core.memory.memory_metrics import memory_metrics

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession
    from src.core.actions.vector_store import VectorStore
    from src.llm.base import LLMProvider

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Phase stubs — return defaults, real logic TBD
# ═══════════════════════════════════════════════════════════════════


async def _run_prune(
    session: AsyncSession,
    user_id: int,
    *,
    vector_store: VectorStore | None = None,
    llm_provider: LLMProvider | None = None,
) -> tuple[int, int]:
    """PRUNE phase — deactivate stale low-confidence facts."""
    from src.core.memory.auto_forget import compaction_prune

    return await compaction_prune(session, user_id)


async def _run_nudge(
    session: AsyncSession,
    user_id: int,
    *,
    vector_store: VectorStore | None = None,
    llm_provider: LLMProvider | None = None,
) -> tuple[int, int, int, int]:
    """NUDGE phase — send confirmation prompts for medium-confidence facts.

    Returns (facts_nudged, nudge_confirmed, nudge_forgotten, nudge_edited).
    Confirmation results arrive asynchronously via callbacks, so the latter
    three counters are always zero at pipeline time.
    """
    from src.core.compaction.nudge import nudge_user
    from src.db.models import User

    user = await session.get(User, user_id)
    if user is None:
        return 0, 0, 0, 0

    facts_nudged = await nudge_user(session, user.telegram_id, user_id)
    return facts_nudged, 0, 0, 0


async def _run_compress(
    session: AsyncSession,
    user_id: int,
    *,
    vector_store: VectorStore | None = None,
    llm_provider: LLMProvider | None = None,
) -> CompressResult:
    """COMPRESS phase — group and merge related facts via LLM."""
    from src.core.compaction.compress import temporal_compress

    min_group = getattr(settings, "compaction_compress_min_group", 3)
    return await temporal_compress(
        session,
        user_id,
        vector_store=vector_store,
        min_group_size=min_group,
        llm_provider=llm_provider,
    )


async def _run_reval(
    session: AsyncSession,
    user_id: int,
    *,
    llm_provider: LLMProvider | None = None,
) -> tuple[int, int]:
    """REVAL phase — re-evaluate stale temporary facts via LLM.

    Returns (reval_examined, reval_changed).
    """
    from src.db.models import User
    from src.core.memory.dreaming_reval import reval_run

    user = await session.get(User, user_id)
    if user is None:
        return 0, 0

    summary = await reval_run(user.telegram_id)
    changed = summary.past + summary.permanent + summary.invalid
    return summary.examined, changed


async def _run_gc(
    session: AsyncSession,
    user_id: int,
    *,
    vector_store: VectorStore | None = None,
) -> int:
    """GC phase — remove Qdrant vectors for deactivated memories in chunks.

    Only processes inactive memories that have a non-empty ``embedding_hash``,
    which acts as the proxy for "a vector was indexed in Qdrant". Qdrant silently
    ignores delete requests for missing point IDs, so we do not need to pre-load
    the full point list.
    """
    if vector_store is None:
        return 0

    from sqlalchemy import select, update as sa_update
    from src.db.models import Memory

    batch_size = int(getattr(settings, "compaction_gc_batch_size", 1000) or 1000)

    total_deleted = 0
    last_id = 0
    while True:
        result = await session.execute(
            select(Memory.id)
            .where(
                Memory.user_id == user_id,
                Memory.is_active.is_(False),
                Memory.id > last_id,
                Memory.embedding_hash.is_not(None),
            )
            .order_by(Memory.id)
            .limit(batch_size)
        )
        ids = [row[0] for row in result.all()]
        if not ids:
            break

        last_id = ids[-1]

        try:
            # Use a nested transaction (savepoint) so a single failed batch
            # does not rollback the work of previously successful batches.
            async with session.begin_nested():
                await vector_store.delete_memories(ids)
                # Sync SQL state so the next run does not reattempt deleted vectors.
                await session.execute(
                    sa_update(Memory)
                    .where(Memory.id.in_(ids))
                    .values(embedding_hash=None)
                )
                total_deleted += len(ids)
        except Exception as exc:
            logger.warning(
                "GC delete_memories failed for batch of %d vectors: %s",
                len(ids),
                exc,
            )
            # Skip this batch and continue; transient errors should not block
            # remaining batches (Qdrant ignores missing IDs).
            continue

        if len(ids) < batch_size:
            break

    return total_deleted


async def _run_learn(
    session: AsyncSession,
    user_id: int,
    *,
    llm_provider: LLMProvider | None = None,
) -> int:
    """LEARN phase — extract reusable skills from successful trajectories."""
    skills = await extract_skills_from_trajectories(
        session, user_id, llm_provider=llm_provider
    )
    return len(skills)


async def _run_metrics(
    session: AsyncSession,
    user_id: int,
    *,
    active_before: int = 0,
    active_after: int = 0,
) -> tuple[int, int, float]:
    """METRICS phase — count active facts and compute compression_ratio.

    Returns (active_before, active_after, compression_ratio).
    """
    # ponytail: simple count query, full metrics logging TBD.
    if active_before == 0 and active_after == 0:
        active = await _count_active_memories(session, user_id)
        return 0, active, 0.0

    ratio = active_after / max(active_before, 1)
    ratio = max(
        0.0, min(1.0, ratio)
    )  # ponytail: clamp, ratio >1 means pipeline added facts
    return active_before, active_after, ratio


async def _run_phase(
    session: AsyncSession,
    name: str,
    coro_factory: Callable[[], Awaitable[Any]],
    errors: list[str],
) -> Any:
    """Run one pipeline phase, commit on success, rollback on failure."""
    try:
        result = await coro_factory()
        await session.commit()
        return result
    except Exception as exc:
        await session.rollback()
        logger.exception("%s phase failed", name)
        errors.append(f"{name}: {exc}")
        return None


# ═══════════════════════════════════════════════════════════════════
#  Orchestrator
# ═══════════════════════════════════════════════════════════════════


async def _count_active_memories(session: AsyncSession, user_id: int) -> int:
    from sqlalchemy import func, select
    from src.db.models import Memory

    result = await session.execute(
        select(func.count()).where(
            Memory.user_id == user_id,
            Memory.is_active.is_(True),
        )
    )
    return result.scalar_one() or 0


def _apply_phase_result(report: CompactionReport, name: str, result: Any) -> None:
    """Unpack a phase result into the corresponding CompactionReport fields."""
    if result is None:
        return
    if name == "PRUNE":
        report.facts_pruned, report.longterm_protected = result
    elif name == "NUDGE":
        (
            report.facts_nudged,
            report.nudge_confirmed,
            report.nudge_forgotten,
            report.nudge_edited,
        ) = result
    elif name == "COMPRESS":
        report.groups_examined = result.groups_examined
        report.groups_compressed = result.groups_compressed
        report.facts_merged = result.facts_merged
    elif name == "REVAL":
        report.reval_examined, report.reval_changed = result
    elif name == "GC":
        report.vectors_removed = result
    elif name == "LEARN":
        report.skills_extracted = result
    elif name == "METRICS":
        report.active_before, report.active_after, report.compression_ratio = result
    else:
        logger.warning(
            "Compaction phase %r has no report mapping; result ignored", name
        )


async def run_compaction_pipeline(owner_telegram_id: int) -> CompactionReport:
    """Run the 7-phase compaction pipeline.

    Each phase runs in its own transaction: commit on success, rollback on
    failure. A failure in one phase does not block the remaining phases.
    Errors are captured in ``report.errors``.

    Returns a ``CompactionReport`` with phase results aggregated.
    """
    from src.db.repo import get_or_create_user
    from src.db.session import get_session
    from src.core.actions.vector_store import get_vector_store
    from src.llm.router import build_provider

    report = CompactionReport()
    started_at = datetime.now(UTC)

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        user_id = owner.id

        active_before = await _count_active_memories(session, user_id)

        try:
            llm_provider = await build_provider(
                session,
                owner,
                task_type="memory",
            )
        except Exception:
            logger.warning(
                "Failed to build LLM provider, continuing without LLM phases"
            )
            llm_provider = None
        vector_store = await get_vector_store()

        phases = [
            (
                "PRUNE",
                lambda: _run_prune(
                    session,
                    user_id,
                    vector_store=vector_store,
                    llm_provider=llm_provider,
                ),
            ),
            (
                "NUDGE",
                lambda: _run_nudge(
                    session,
                    user_id,
                    vector_store=vector_store,
                    llm_provider=llm_provider,
                ),
            ),
            (
                "COMPRESS",
                lambda: _run_compress(
                    session,
                    user_id,
                    vector_store=vector_store,
                    llm_provider=llm_provider,
                ),
            ),
            (
                "REVAL",
                lambda: _run_reval(
                    session,
                    user_id,
                    llm_provider=llm_provider,
                ),
            ),
            (
                "GC",
                lambda: _run_gc(
                    session,
                    user_id,
                    vector_store=vector_store,
                ),
            ),
            (
                "LEARN",
                lambda: _run_learn(
                    session,
                    user_id,
                    llm_provider=llm_provider,
                ),
            ),
        ]
        for name, coro_factory in phases:
            result = await _run_phase(session, name, coro_factory, report.errors)
            _apply_phase_result(report, name, result)

        # Metrics needs the post-phase active count.
        active_after = await _count_active_memories(session, user_id)
        metrics_result = await _run_phase(
            session,
            "METRICS",
            lambda: _run_metrics(
                session,
                user_id,
                active_before=active_before,
                active_after=active_after,
            ),
            report.errors,
        )
        _apply_phase_result(report, "METRICS", metrics_result)

        if llm_provider is not None:
            try:
                await llm_provider.close()
            except Exception:
                logger.debug("Non-critical error closing LLM provider", exc_info=True)
        # VectorStore is a singleton managed by the application lifecycle;
        # do NOT shut it down here.

    try:
        await memory_metrics.record_compaction(report)
    except Exception:
        logger.debug("Failed to record compaction metrics", exc_info=True)

    report.duration_sec = (datetime.now(UTC) - started_at).total_seconds()
    return report
