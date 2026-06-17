"""Auto-forget: deactivate memories with retention below Ebbinghaus threshold."""

import asyncio
import logging
import math
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, UTC

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.compaction.filters import non_task_memory_type_filter
from src.core.memory.memory_recall import bump_recall_version
from src.core.memory.temporal_layers import compute_retention, utc_naive
from src.db.models._base import User
from src.db.models._memory import Memory

logger = logging.getLogger(__name__)


def _filter_low_retention(
    memories: list[Memory],
    now: datetime,
    threshold: float,
    decay_base: float,
    access_weight: float,
) -> list[int]:
    """CPU-bound retention check for a single batch."""
    return [
        m.id
        for m in memories
        if compute_retention(
            m,
            now,
            decay_base=decay_base,
            access_weight=access_weight,
        )
        < threshold
    ]


async def _iter_fact_batches(
    session: AsyncSession,
    user_id: int,
    batch_size: int,
) -> AsyncGenerator[list[Memory]]:
    """Yield active, non-pinned, non-task memory batches in ascending id order."""
    base_filter = (
        Memory.user_id == user_id,
        Memory.is_active.is_(True),
        Memory.pinned.is_(False),
        non_task_memory_type_filter(),
    )
    last_id = 0
    while True:
        result = await session.execute(
            select(Memory)
            .where(*base_filter, Memory.id > last_id)
            .order_by(Memory.id)
            .limit(batch_size)
        )
        batch = list(result.scalars().all())
        if not batch:
            break
        yield batch
        last_id = batch[-1].id
        if len(batch) < batch_size:
            break


async def _deactivate_facts(
    session: AsyncSession,
    ids: list[int],
    now: datetime,
) -> None:
    """Deactivate a batch of facts by id, guarding against double-deactivation."""
    if not ids:
        return
    await session.execute(
        sa_update(Memory)
        .where(Memory.id.in_(ids), Memory.is_active.is_(True))
        .values(
            is_active=False,
            validity_end=now,
            updated_at=now,
        )
    )
    await session.flush()


async def auto_forget_sweep(session: AsyncSession, user_id: int) -> int:
    """Find and deactivate memories with retention < auto_forget_threshold.

    Processes candidates in chunks to avoid loading all rows into memory at once.
    Returns: number of deactivated facts.
    """
    if not settings.auto_forget_enabled:
        return 0

    threshold = settings.auto_forget_threshold
    now = datetime.now(UTC)
    batch_size = int(getattr(settings, "auto_forget_batch_size", 1000) or 1000)

    total_deactivated = 0

    async for batch in _iter_fact_batches(session, user_id, batch_size):
        to_deactivate = await asyncio.to_thread(
            _filter_low_retention,
            batch,
            now,
            threshold,
            settings.ebbinghaus_decay_base,
            settings.ebbinghaus_access_weight,
        )
        if to_deactivate:
            await _deactivate_facts(session, to_deactivate, now)
            total_deactivated += len(to_deactivate)

    if total_deactivated:
        # Invalidate recall cache for this user
        user_row = await session.execute(
            select(User.telegram_id).where(User.id == user_id)
        )
        uid = user_row.scalar_one_or_none()
        if uid is not None:
            await bump_recall_version(uid)
            # B6: инвалидируем stats-кэш (mem_*), чтобы health/coverage
            # не показывали stale значения.
            from src.core.actions.stats_cache import invalidate

            await invalidate("mem_")

    logger.info(
        "Auto-forget: deactivated %d facts for user %d (threshold=%.2f)",
        total_deactivated,
        user_id,
        threshold,
    )
    return total_deactivated


def _prune_batch(
    batch: list[Memory],
    now: datetime,
    base_threshold: float,
    longterm_mult: float,
    cutoff_zero_use: datetime,
    decay_base: float,
    access_weight: float,
) -> tuple[list[int], int]:
    """CPU-bound retention/zero-use check for one compaction_prune batch."""
    to_deactivate: list[int] = []
    longterm_protected = 0
    for m in batch:
        layer = m.temporal_layer or "medium"
        is_longterm = layer == "longterm"

        effective_retention = compute_retention(
            m,
            now,
            decay_base=decay_base,
            access_weight=access_weight,
            decay_multiplier=longterm_mult if is_longterm else 1.0,
        )

        prune_by_retention = effective_retention < base_threshold
        prune_by_zero_use = (
            not is_longterm
            and (m.use_count or 0) == 0
            and m.last_used_at is None
            and m.created_at is not None
            and utc_naive(m.created_at) < utc_naive(cutoff_zero_use)
        )

        if prune_by_retention or prune_by_zero_use:
            to_deactivate.append(m.id)
        elif is_longterm:
            base_retention = math.pow(effective_retention, longterm_mult)
            if base_retention < base_threshold:
                longterm_protected += 1

    return to_deactivate, longterm_protected


async def compaction_prune(
    session: AsyncSession,
    user_id: int,
    *,
    longterm_mult: float = 10.0,
    zero_use_days: int = 30,
) -> tuple[int, int]:
    """Two-factor prune for the Compaction Pipeline v2.

    Combines Ebbinghaus retention with a zero-use cutoff: long-term facts decay
    ``longterm_mult`` times slower, and unused medium/recent facts older than
    ``zero_use_days`` are also pruned. Pinned and task facts are never touched.

    Processes candidates in chunks to avoid loading all rows into memory.
    Returns (deactivated_count, longterm_protected_count).
    """
    if not getattr(settings, "auto_forget_enabled", True):
        return 0, 0

    base_threshold = settings.auto_forget_threshold
    now = datetime.now(UTC)
    cutoff_zero_use = now - timedelta(days=zero_use_days)
    batch_size = int(getattr(settings, "compaction_prune_batch_size", 1000) or 1000)

    total_deactivated = 0
    longterm_protected = 0

    async for batch in _iter_fact_batches(session, user_id, batch_size):
        to_deactivate, protected = await asyncio.to_thread(
            _prune_batch,
            batch,
            now,
            base_threshold,
            longterm_mult,
            cutoff_zero_use,
            settings.ebbinghaus_decay_base,
            settings.ebbinghaus_access_weight,
        )
        longterm_protected += protected

        if to_deactivate:
            await _deactivate_facts(session, to_deactivate, now)
            total_deactivated += len(to_deactivate)

    logger.info(
        "Compaction prune: deactivated %d facts, longterm protected %d for user %d",
        total_deactivated,
        longterm_protected,
        user_id,
    )
    return total_deactivated, longterm_protected
