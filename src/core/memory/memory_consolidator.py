"""Memory Consolidation — periodic merge of near-duplicate facts."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC

from src.db.repo import list_memories, get_or_create_user
from src.db.session import get_session
from src.config import settings
from src.core.memory.memory_recall import bump_recall_version
from src.core.infra.task_manager import task_manager

logger = logging.getLogger(__name__)

_overlap_guard = asyncio.Lock()

# Similarity threshold for consolidation
SIM_THRESHOLD = 0.85


async def consolidate_memories(telegram_id: int) -> int:
    """Find and merge near-duplicate facts. Returns count of merged pairs."""
    merged = 0
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        memories = await list_memories(session, owner, limit=500)

        # Cap input to prevent O(n²) explosion
        if len(memories) > 100:
            memories = sorted(
                memories,
                key=lambda m: m.updated_at or m.created_at,
                reverse=True,
            )[:100]

        # Cap comparisons at 5000
        _max_pairs = min(len(memories) * (len(memories) - 1) // 2, 5000)
        pairs_checked = 0
        for i, m1 in enumerate(memories):
            if not m1.is_active or not m1.fact:
                continue
            for j in range(i + 1, len(memories)):
                if pairs_checked >= _max_pairs:
                    break
                m2 = memories[j]
                if not m2.is_active or not m2.fact:
                    continue

                # Check similarity (simple word overlap for speed)
                words1 = set(m1.fact.lower().split())
                words2 = set(m2.fact.lower().split())
                if not words1 or not words2:
                    continue
                overlap = len(words1 & words2) / max(len(words1), len(words2))

                if overlap >= SIM_THRESHOLD and m1.contact_id == m2.contact_id:
                    # Не деактивируем закреплённые (pinned) факты —
                    # пользователь явно пометил их как важные.
                    if m2.pinned:
                        continue
                    # Merge: boost confidence of older fact, deactivate newer
                    avg_conf = ((m1.confidence or 0.5) + (m2.confidence or 0.5)) / 2
                    m1.confidence = min(1.0, avg_conf * 1.1)
                    m2.is_active = False
                    # M10: устанавливаем validity_end — без него деактивированный
                    # факт не имеет временной метки деактивации, что ломает
                    # запросы фильтрации по времени и health-метрики.
                    m2.validity_end = datetime.now(UTC)
                    m2.memory_tier = 0  # archived
                    merged += 1
                    logger.debug(
                        "Merged: '%s' ← '%s'",
                        m1.fact[:50],
                        m2.fact[:50],
                    )
                pairs_checked += 1
            if pairs_checked >= _max_pairs:
                break

        await session.flush()
        await bump_recall_version(telegram_id)
        # B6: инвалидируем stats-кэш после слияния дубликатов
        from src.core.actions.stats_cache import invalidate

        await invalidate("mem_")
    return merged


async def consolidation_loop() -> None:
    """Periodic consolidation — runs every 6 hours."""
    while True:
        async with _overlap_guard:
            try:
                count = await consolidate_memories(settings.owner_telegram_id)
                if count:
                    logger.info("Memory consolidation: merged %d duplicate pairs", count)
            except Exception:
                logger.exception("Consolidation failed")
        await asyncio.sleep(settings.memory_consolidation_interval_sec)  # 6 hours


# NOTE: originally planned to move to dream_cycle.py (unified nightly job),
# but that module is not yet imported. Standalone registration for now.
# TODO(B3-fix): migrate to dream_cycle.py once dream_cycle.py is importable.
task_manager.register("memory-consolidator", consolidation_loop)
