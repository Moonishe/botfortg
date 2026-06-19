"""Memory service layer — single-fact + batch memory writes + Core side effects.

This module decouples the DB repository from Core concerns for both
the **single-fact** and **batch** save paths.

* ``_add_memory_core`` / ``_delete_memory_core`` in
  ``src.db.repos.memory_repo._core`` are pure DB operations.
* All cache invalidation, recall-version bumps, auto-linking, hooks,
  Qdrant indexing, and contact-digest invalidation live here.

Entry points:

* ``save_memory_single`` — save or merge ONE fact, with full side effects.
* ``delete_memory_service`` — soft-delete ONE fact, with side effects.
* ``save_memories_batch`` — batch save (no per-fact side effects).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.actions.stats_cache import invalidate
from src.core.actions.vector_store import get_vector_store
from src.core.contacts.contact_memory_digest import invalidate_contact_digest
from src.core.memory.fact_quality import enrich_facts
from src.core.memory.memory_recall import bump_recall_version
from src.db.models import Contact, Memory, User
from src.db.repo import add_memories, get_or_create_user
from src.db.session import get_session

if TYPE_CHECKING:
    from src.core.actions.vector_store import VectorStore

logger = logging.getLogger(__name__)


async def save_memory_single(
    session: AsyncSession,
    user: User,
    *,
    fact: str,
    contact_id: int | None = None,
    sentiment: str | None = None,
    source: str = "user",
    confidence: float = 0.85,
    message_id: int | None = None,
    cluster_topic: str | None = None,
    deduplicate: bool = True,
    embedding: list[float] | None = None,
    vector_store_obj: VectorStore | None = None,
    importance: float | None = None,
    decay_rate: float | None = None,
    memory_tier: int | None = None,
    memory_type: str | None = "contact_fact",
    pinned: bool | None = None,
    expires_at: datetime | None = None,
    use_count: int | None = None,
    source_quality: float | None = None,
    extraction_quality: float | None = None,
    is_active: bool = True,
) -> Memory | None:
    """Save a single fact to memory with full side effects.

    Validates input, calls the pure-DB ``_add_memory_core`` for
    create-or-merge, then applies side effects:

    1. **Auto-link** — connect to related facts (best-effort).
    2. **Hooks** — emit ``on_memory_saved`` (best-effort).
    3. **Qdrant indexing** — upsert embedding for future dedup (best-effort).
    4. **Cache invalidation** — ``invalidate("mem_")``.
    5. **Recall-version bump** — ``bump_recall_version``.
    6. **Contact-digest invalidation** — if ``contact_id`` is set.

    Args:
        session: Active DB session.
        user: Owner (User ORM object).
        fact: Fact text (will be stripped).
        contact_id: Optional contact peerid.
        sentiment: ``positive``, ``negative``, ``neutral``, or ``None``.
        source: ``chat``, ``user``, ``auto``, or ``weekly``.
        confidence: 0.0–1.0 initial confidence.
        deduplicate: Whether to merge duplicates or force a new record.
        embedding: Optional vector for Qdrant dedup + indexing.
        vector_store_obj: Optional VectorStore for semantic dedup + indexing.
        is_active: Whether the memory is active (default True).
        (other params forwarded to ``_add_memory_core``).

    Returns:
        The created or merged :class:`Memory`, or ``None`` if fact is too short.
    """
    # ── Validation ───────────────────────────────────────────────────────
    fact = fact.strip()
    if len(fact) < 3:
        return None

    # Normalize sentiment rather than failing callers.
    if sentiment is not None and sentiment not in ("positive", "negative", "neutral"):
        sentiment = "neutral"

    # ── Pure-DB create-or-merge ──────────────────────────────────────────
    # Lazy import to avoid circular: memory_service → _add_memory_core → session_repo
    from src.db.repos.memory_repo._core import _add_memory_core

    mem, created_new = await _add_memory_core(
        session,
        user,
        fact=fact,
        contact_id=contact_id,
        sentiment=sentiment,
        source=source,
        confidence=confidence,
        message_id=message_id,
        cluster_topic=cluster_topic,
        deduplicate=deduplicate,
        embedding=embedding,
        vector_store_obj=vector_store_obj,
        importance=importance,
        decay_rate=decay_rate,
        memory_tier=memory_tier,
        memory_type=memory_type,
        pinned=pinned,
        expires_at=expires_at,
        use_count=use_count,
        source_quality=source_quality,
        extraction_quality=extraction_quality,
        is_active=is_active,
    )

    if mem is None:
        return None

    # ── Side effects (only for newly created memories) ───────────────────
    if created_new:
        # 1. Auto-link — connect to related facts (best-effort)
        try:
            # Auto-link lives in core layer (it depends on Qdrant and
            # RelationType), so import direction is core → db.
            from src.core.memory.auto_linker import auto_link_memory

            await auto_link_memory(session, user, mem, embedding=embedding)
        except Exception as exc:
            logger.warning(
                "Auto-link failed for memory %d — memory saved anyway: %s",
                mem.id,
                exc,
            )
            logger.debug("Auto-link failure traceback", exc_info=True)

        # 2. Hooks — emit on_memory_saved (best-effort)
        try:
            from src.core.infra.hooks import hooks

            contact_name: str | None = None
            if contact_id is not None:
                try:
                    contact_result = await session.execute(
                        select(Contact.display_name).where(
                            Contact.user_id == user.id,
                            Contact.peer_id == contact_id,
                        )
                    )
                    contact_name = contact_result.scalar_one_or_none()
                except SQLAlchemyError:
                    contact_name = None

            await hooks.emit(
                "on_memory_saved",
                memory_id=mem.id,
                fact=fact,
                user_id=user.telegram_id,
                contact_id=contact_id,
                contact_name=contact_name,
                confidence=confidence,
            )
        except Exception:
            logger.debug(
                "on_memory_saved hook failed for memory %d", mem.id, exc_info=True
            )

        # 3. Qdrant indexing — upsert embedding for future dedup
        if embedding is not None and vector_store_obj is not None:
            try:
                await vector_store_obj.upsert_memory(
                    memory_id=mem.id,
                    user_id=user.id,
                    contact_id=contact_id,
                    fact=fact,
                    embedding=embedding,
                )
            except Exception as exc:
                logger.error(
                    "CRITICAL: Failed to index memory %d in Qdrant — "
                    "fact saved in SQLite but NOT searchable via vector. "
                    "Re-index required: memory_id=%d user_id=%d error=%s",
                    mem.id,
                    mem.id,
                    user.id,
                    exc,
                )
                logger.debug("Qdrant indexing failure traceback", exc_info=True)

    # 4–6. Always-run side effects (even on merge)
    await invalidate("mem_")
    await bump_recall_version(user.telegram_id)
    if contact_id is not None:
        await invalidate_contact_digest(contact_id)

    return mem


async def delete_memory_service(
    session: AsyncSession,
    user: User,
    memory_id: int,
    *,
    vector_store_obj: VectorStore | None = None,
) -> bool:
    """Soft-delete a Memory with full side effects.

    Calls the pure-DB ``_delete_memory_core``, then:

    1. Deletes the Qdrant point (best-effort).
    2. ``invalidate("mem_")``
    3. ``bump_recall_version(user.telegram_id)``
    4. ``invalidate_contact_digest(contact_id)`` (if applicable).

    Args:
        session: Active DB session.
        user: Owner (User ORM object).
        memory_id: ID of the Memory to delete.
        vector_store_obj: Optional VectorStore to clean up the Qdrant point.
            If not provided, the singleton VectorStore is used.

    Returns:
        ``True`` if deleted, ``False`` if not found or not owned.
    """
    from src.db.repos.memory_repo._core import _delete_memory_core

    success, contact_id = await _delete_memory_core(session, user, memory_id)

    if success:
        # Qdrant cleanup — best-effort; SQLite is the source of truth.
        try:
            vs = vector_store_obj or await get_vector_store()
            await vs.delete_memories([memory_id])
        except Exception as exc:
            logger.warning(
                "Qdrant cleanup failed for deleted memory %d — "
                "point may remain in vector index: %s",
                memory_id,
                exc,
            )
            logger.debug("Qdrant cleanup failure traceback", exc_info=True)

        await invalidate("mem_")
        await bump_recall_version(user.telegram_id)
        if contact_id is not None:
            await invalidate_contact_digest(contact_id)

    return success


async def bulk_delete_memory_service(
    session: AsyncSession,
    user: User,
    memory_ids: list[int],
    *,
    vector_store_obj: VectorStore | None = None,
) -> int:
    """Batch soft-delete Memories with full side effects.

    Returns the number of actually deleted memories.
    """
    from src.db.repos.memory_repo._core import _bulk_delete_memory_core

    if not memory_ids:
        return 0

    deleted_ids, contact_ids = await _bulk_delete_memory_core(
        session, user, memory_ids
    )

    if deleted_ids:
        # Qdrant cleanup — best-effort
        try:
            vs = vector_store_obj or await get_vector_store()
            await vs.delete_memories(deleted_ids)
        except Exception as exc:
            logger.warning(
                "Qdrant bulk cleanup failed for deleted memories %s: %s",
                deleted_ids,
                exc,
            )
            logger.debug("Qdrant bulk cleanup failure traceback", exc_info=True)

        await invalidate("mem_")
        await bump_recall_version(user.telegram_id)
        for cid in set(contact_ids):
            await invalidate_contact_digest(cid)

    return len(deleted_ids)


async def save_memories_batch(
    telegram_id: int,
    facts: list[dict[str, Any]],
    *,
    source: str = "auto",
    contact_id: int | None = None,
) -> int:
    """Сохраняет батч фактов в память и обновляет зависимые кэши.

    Пайплайн:
      1. enrich_facts() — фильтрация + quality-оценка
      2. get_or_create_user() + add_memories() (чистый DB-уровень)
      3. invalidate("mem_") + bump_recall_version() + invalidate_contact_digest()

    Args:
        telegram_id: Telegram ID владельца фактов.
        facts: Список словарей с ключом ``fact`` (и опционально ``sentiment``).
        source: Источник фактов (по умолчанию ``auto``).
        contact_id: Опциональный ID контакта для инвалидации дайджеста.

    Returns:
        Количество сохранённых/обновлённых фактов.

    Raises:
        ValueError: на некорректных входных данных.
    """
    if (
        not isinstance(telegram_id, int)
        or isinstance(telegram_id, bool)
        or telegram_id <= 0
    ):
        raise ValueError("telegram_id must be a positive integer")
    if not isinstance(facts, list):
        raise ValueError("facts must be a list")
    # ponytail: batch path skips rich single-fact side effects (auto-linking,
    # on_memory_saved hooks) for performance. Single-fact path still provides them.
    facts = [f for f in facts if isinstance(f, dict)]
    if not facts:
        return 0

    enriched = enrich_facts(facts)
    if not enriched:
        return 0

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        stored = await add_memories(
            session,
            owner,
            enriched,
            source=source,
            contact_id=contact_id,
        )

    if stored:
        await invalidate("mem_")
        await bump_recall_version(telegram_id)
        if contact_id is not None:
            await invalidate_contact_digest(contact_id)

    return stored
