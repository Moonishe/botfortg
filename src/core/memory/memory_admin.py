"""DB helpers for memory administration: queries, deactivation, text updates, supersedes links.

This module provides shared database access functions extracted from
:mod:`src.core.memory.dreaming_reval` for reuse across the memory subsystem.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models._base import User
from src.db.models._memory import Memory, MemoryLink

logger = logging.getLogger(__name__)

__all__ = [
    "ALLOWED_MEMORY_TYPES",
    "MAX_FACT_LEN",
    "MIN_FACT_LEN",
    "add_supersedes_link",
    "deactivate_memory",
    "select_old_temporary_facts",
    "update_memory_text",
]

# ── Shared constants ─────────────────────────────────────────────────

# Whitelist of allowed memory types — shared across reval and manual corrections.
ALLOWED_MEMORY_TYPES: frozenset[str] = frozenset(
    {
        "contact_fact",
        "personal",
        "relationship",
        "preference",
        "task",
        "general",
        "l2_policy",
    }
)
MAX_FACT_LEN = 500
MIN_FACT_LEN = 3


# ── Queries ──────────────────────────────────────────────────────────


async def select_old_temporary_facts(
    session: AsyncSession,
    user_id: int,
    *,
    limit: int = 50,
    confidence_threshold: float = 0.5,
    lookback_days: int | None = None,
) -> list[Memory]:
    """Select active temporary facts older than 7 days with high confidence.

    Filters:
      - ``is_active=True``
      - ``pinned=False``
      - ``confidence >= confidence_threshold``
      - ``memory_type IN ('temporary', 'task')``
      - ``created_at`` older than 7 days (don't reval fresh facts)
      - within *lookback_days* (don't touch very old facts)

    Returns up to *limit* memories ordered oldest-first.
    """
    now = datetime.now(UTC)
    cutoff_old = now - timedelta(days=7)
    cutoff_recent = now - timedelta(
        days=lookback_days if lookback_days is not None else 365
    )

    result = await session.execute(
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.is_active.is_(True),
            Memory.pinned.is_(False),
            Memory.confidence >= confidence_threshold,
            Memory.memory_type.in_(("temporary", "task")),
            Memory.created_at < cutoff_old,
            Memory.created_at > cutoff_recent,
        )
        .order_by(Memory.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ── Mutations ────────────────────────────────────────────────────────


async def deactivate_memory(
    session: AsyncSession,
    memory_id: int,
    *,
    reason: str,
    user_id: int,
) -> None:
    """Mark a memory inactive.

    Sets ``is_active=False`` and updates ``updated_at``. Does *not* delete —
    history is preserved for audit and undo.

    Acquires the per-user lock to prevent races with concurrent
    ``_add_memory_core``, which may merge into a memory after we've
    decided it should be deactivated.

    Args:
        session: Active DB session.
        memory_id: ID of the memory row to deactivate.
        reason: Short explanation (e.g. ``"reval_invalid"``, ``"manual_reject"``).
        user_id: Owner ID for ownership validation.
            If the memory belongs to a different user,
            deactivation is silently skipped.
    """
    from src.db.repos.session_repo import _get_user_lock

    lock = _get_user_lock(user_id)
    async with lock:
        mem = await session.get(Memory, memory_id)
        if not mem or mem.user_id is None:
            return
        if mem.user_id != user_id:
            logger.warning(
                "deactivate_memory: memory_id=%d does not belong to user %d (got %d)",
                memory_id,
                mem.user_id,
                user_id,
            )
            return
        mem.is_active = False
        mem.updated_at = datetime.now(UTC)
        await session.flush()
        logger.info("Deactivated memory %d (reason=%s)", memory_id, reason)


async def update_memory_text(
    session: AsyncSession,
    user: User,
    memory_id: int,
    new_fact: str,
    *,
    new_memory_type: str | None = None,
    new_decay_rate: float | None = None,
    request_version: datetime | None = None,
    edit_reason: str | None = None,
) -> Memory | None:
    """In-place update of fact text + optional type/decay.

    Also bumps ``embedding_hash`` so the dedup layer treats this as a new fact
    (prevents merge-back with the old version).

    Оптимистическая блокировка: если передан *request_version*,
    сравнивается с ``mem.updated_at`` — при расхождении выбрасывается
    ``ValueError`` (ConflictError).

    Returns the updated ``Memory`` row or ``None`` if the fact text is invalid
    or the memory does not exist.
    """
    new_fact = new_fact.strip()
    if not (MIN_FACT_LEN <= len(new_fact) <= MAX_FACT_LEN):
        return None
    mem = await session.get(Memory, memory_id)
    if not mem:
        return None

    # Ownership check — must match before modifying the fact
    if mem.user_id != user.id:
        logger.warning(
            "update_memory_text: memory_id=%d does not belong to user %d",
            memory_id,
            user.id,
        )
        return None

    # Оптимистическая блокировка: проверяем, что факт не был изменён
    # параллельно с момента чтения клиентом.
    if request_version is not None and mem.updated_at != request_version:
        raise ValueError(
            f"Конфликт версий: memory {memory_id} был изменён "
            f"({mem.updated_at} != {request_version})"
        )

    mem.fact = new_fact
    mem.embedding_hash = hashlib.sha256(new_fact.lower().encode()).hexdigest()[:16]

    # Сохраняем версию в аудит-трейл
    from src.db.repos.memory_repo import save_memory_version

    await save_memory_version(
        session, user, memory_id, new_fact, edited_by="user", reason=edit_reason
    )

    if new_memory_type is not None and new_memory_type in ALLOWED_MEMORY_TYPES:
        mem.memory_type = new_memory_type
    if new_decay_rate is not None:
        mem.decay_rate = max(0.01, min(0.30, new_decay_rate))
    mem.updated_at = datetime.now(UTC)
    await session.flush()

    # Инвалидация кэша: сбрасываем recall-кэш и stats-кэш владельца
    from src.core.actions.stats_cache import invalidate
    from src.core.memory.memory_recall import bump_recall_version
    from src.core.events.event_bus import event_bus, MEMORY_MUTATED
    from src.db.models._base import User

    user_result = await session.execute(
        select(User.telegram_id).where(User.id == mem.user_id)
    )
    owner_telegram_id = user_result.scalar_one_or_none()
    if owner_telegram_id is not None:
        await invalidate("mem_")
        await bump_recall_version(owner_telegram_id)
        await event_bus.emit(
            MEMORY_MUTATED, user_id=owner_telegram_id, action="update_text"
        )

    logger.info("Updated memory %d → new text len=%d", memory_id, len(new_fact))
    return mem


async def add_supersedes_link(
    session: AsyncSession,
    user_id: int,
    *,
    old_id: int,
    new_id: int,
    confidence: float = 1.0,
    relation_type: str = "supersedes",
) -> MemoryLink | None:
    """Create a ``MemoryLink(old → new)`` with the given *relation_type*.

    Idempotent: returns ``None`` if the same link already exists, if
    ``old_id == new_id``, or if the new memory does not exist.

    Args:
        session: Active DB session.
        user_id: Owner of the memory link.
        old_id: Source memory ID (the superseded fact).
        new_id: Target memory ID (the new fact).
        confidence: Weight of the link (default ``1.0``).
        relation_type: Type of relationship (default ``"supersedes"``).
    """
    if old_id == new_id:
        return None
    # Verify both memories belong to the user (defense-in-depth).
    old_mem = await session.get(Memory, old_id)
    new_mem = await session.get(Memory, new_id)
    if not old_mem or not new_mem:
        return None
    if old_mem.user_id != user_id or new_mem.user_id != user_id:
        logger.warning(
            "add_supersedes_link: ownership mismatch (user_id=%d, old=%d, new=%d)",
            user_id,
            old_id,
            new_id,
        )
        return None
    # Check existing
    existing_q = await session.execute(
        select(MemoryLink).where(
            MemoryLink.user_id == user_id,
            MemoryLink.source_id == old_id,
            MemoryLink.target_id == new_id,
            MemoryLink.relation_type == relation_type,
        )
    )
    if existing_q.scalar_one_or_none() is not None:
        return None
    link = MemoryLink(
        user_id=user_id,
        source_id=old_id,
        target_id=new_id,
        relation_type=relation_type,
        weight=confidence,
    )
    session.add(link)
    await session.flush()
    logger.info(
        "Created %s link: %d → %d",
        relation_type,
        old_id,
        new_id,
    )
    return link
