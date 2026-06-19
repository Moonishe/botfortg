"""Memory repository — core CRUD operations (pure DB, NO side effects)."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, UTC
from typing import Any, TYPE_CHECKING

from sqlalchemy import case, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Memory, MemoryLink, User

if TYPE_CHECKING:
    from src.core.actions.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Default TTL extension for memories that corroborate an existing fact.
# ponytail: hardcoded days; move to settings if per-user or per-tier TTL needed.
_DEFAULT_MEMORY_TTL_DAYS: int = 30

# Confidence bump weights used by merge logic in both single-fact and batch paths.
_SOURCE_WEIGHT_MAP: dict[str, float] = {"chat": 0.3, "user": 0.6, "weekly": 0.15}

# Temporal markers force a new record — never merge.
# Use word-boundary matching (re.search with \b) for multi-word
# markers to avoid false positives: e.g. "уже не" should NOT match
# "уже некуда" or "больше некого".
_TEMPORAL_RE = re.compile(
    r"\b(сейчас|раньше|перестал)\b|\bуже\s+не\b|\bбольше\s+не\b",
    re.IGNORECASE,
)


# ── Whitelist допустимых relation_type для MemoryLink ────────────────
# Используется в link_memories() для отсева LLM-галлюцинаций вроде
# «supersede» (без 's') или «replaces». Полный список должен совпадать с
# RELATION_EMOJI.keys() в src/core/memory/memory_chain.py и с LLM-промптом
# MEMORIES_SYSTEM в src/core/memory/memory_extractor.py.
# Канонические константы: src.core.memory.relation_types.RelationType.
_VALID_RELATION_TYPES: frozenset[str] = frozenset(
    {
        "cause",
        "effect",
        "contradicts",
        "supports",
        "continues",
        "example_of",
        "supersedes",
        "co_temporal",
        "co_entity",
        "preceded",
    }
)


def _normalize_to_utc(dt: datetime) -> datetime:
    """Convert datetime to UTC-aware. Naive datetimes assumed UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _extend_memory_expiry(
    existing: Memory,
    new_expires_at: datetime | None,
    ttl_days: int = _DEFAULT_MEMORY_TTL_DAYS,
) -> None:
    """Extend ``existing.expires_at`` when a fact corroborates it.

    - If ``new_expires_at`` is provided → use it (or the existing date,
      whichever is later).
    - If only ``existing.expires_at`` is set → extend it by ``ttl_days``
      from now.
    - If neither has an expiry → leave ``None``.

    Both existing and new dates are normalised to UTC-aware before comparison
    to prevent ``TypeError`` from naive vs. aware datetime mixing.
    """
    now = datetime.now(UTC)
    if new_expires_at is not None:
        new_norm = _normalize_to_utc(new_expires_at)
        if existing.expires_at is not None:
            existing.expires_at = max(_normalize_to_utc(existing.expires_at), new_norm)
        else:
            existing.expires_at = new_norm
    elif existing.expires_at is not None:
        existing_norm = _normalize_to_utc(existing.expires_at)
        extended = now + timedelta(days=ttl_days)
        existing.expires_at = max(existing_norm, extended)


# ── Core memory operations (pure DB, NO side effects) ─────────────────
# These are the "clean" DB-only functions. All side effects (cache
# invalidation, hooks, Qdrant indexing, recall-version bumps, contact
# digest invalidation) live in src.core.memory.memory_service.
def _merge_into_existing(
    existing: Memory,
    *,
    expires_at: datetime | None,
    source_weight: float,
    sentiment: str | None,
) -> None:
    """Update *existing* with a new corroboration occurrence."""
    now = datetime.now(UTC)
    _extend_memory_expiry(existing, expires_at)
    existing.times_mentioned = (existing.times_mentioned or 1) + 1
    base_confidence = existing.confidence if existing.confidence is not None else 0.5
    existing.confidence = min(1.0, base_confidence + source_weight)
    existing.corroboration_count = (existing.corroboration_count or 0) + 1
    existing.last_corroborated_at = now
    existing.updated_at = now
    # Only mark contradictory if we HAD a different sentiment before.
    # None → new_sentiment is not a contradiction — it is a first sentiment.
    if sentiment and existing.sentiment is not None and existing.sentiment != sentiment:
        existing.sentiment = "contradictory"
    elif sentiment and existing.sentiment is None:
        existing.sentiment = sentiment


async def _find_exact_match(
    session: AsyncSession,
    user: User,
    emb_hash: str,
) -> Memory | None:
    """Return active memory owned by *user* with matching embedding_hash."""
    result = await session.execute(
        select(Memory)
        .where(
            Memory.user_id == user.id,
            Memory.embedding_hash == emb_hash,
            Memory.is_active.is_(True),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _add_memory_core(
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
) -> tuple[Memory | None, bool]:
    """Pure-DB create-or-merge for a single fact. NO side effects.

    Performs SHA256 dedup (when ``deduplicate=True``), optional Qdrant
    semantic dedup, and either merges into an existing Memory or creates
    a new one. Returns ``(memory, created_new)`` — ``created_new`` is
    ``True`` only when a fresh row is inserted; ``False`` on merge or if
    fact is too short.

    Qdrant search is executed *outside* the per-user lock so that slow
    vector queries do not block other operations for the same user.

    All side effects (auto-linking, hooks, Qdrant indexing, cache
    invalidation, recall-version bump, contact-digest invalidation)
    are the caller's responsibility (see
    :func:`src.core.memory.memory_service.save_memory_single`).
    """
    from src.db.repos.session_repo import _get_user_lock

    fact = fact.strip()
    if len(fact) < 3 or len(fact) > 10000:
        return None, False

    # SHA256 hash for exact dedup (first 64 bits)
    emb_hash = hashlib.sha256(fact.lower().strip().encode()).hexdigest()[:16]

    # source → confidence weight for merge
    source_weight = _SOURCE_WEIGHT_MAP.get(source, 0.3)

    has_temporal_marker = bool(_TEMPORAL_RE.search(fact))
    do_dedup = deduplicate and not has_temporal_marker

    lock = _get_user_lock(user.id)

    # ── Level 1: SHA256 exact match (under lock) ───────────────────────
    if do_dedup:
        async with lock:
            existing = await _find_exact_match(session, user, emb_hash)
            if existing:
                _merge_into_existing(
                    existing,
                    expires_at=expires_at,
                    source_weight=source_weight,
                    sentiment=sentiment,
                )
                await session.flush()
                return existing, False

    # ── Level 2: Qdrant semantic dedup (outside lock) ───────────────────
    qdrant_best: dict[str, Any] | None = None
    if do_dedup and embedding is not None and vector_store_obj is not None:
        try:
            similar = await vector_store_obj.search_similar_memories(
                user_id=user.id,
                embedding=embedding,
                threshold=0.7,
                limit=3,
            )
        except Exception:
            # Qdrant is advisory for dedup; on failure just create a new memory
            logger.debug("Qdrant dedup failed, creating new memory", exc_info=True)
            similar = []
        if similar:
            qdrant_best = similar[0]

    # ── Level 1/2 re-check + create new memory (under lock) ──────────────
    async with lock:
        if do_dedup:
            # Re-check SHA256: another task may have inserted while we were
            # talking to Qdrant.
            existing = await _find_exact_match(session, user, emb_hash)
            if existing:
                _merge_into_existing(
                    existing,
                    expires_at=expires_at,
                    source_weight=source_weight,
                    sentiment=sentiment,
                )
                await session.flush()
                return existing, False

            if qdrant_best:
                qdrant_mid = qdrant_best.get("memory_id")
                if qdrant_mid is not None:
                    existing = await session.get(Memory, qdrant_mid)
                else:
                    existing = None
                if existing and existing.user_id == user.id and existing.is_active:
                    now = datetime.now(UTC)
                    age_days = (
                        (now - existing.created_at).days if existing.created_at else 999
                    )
                    same_source = existing.source == source
                    if same_source and age_days < 7:
                        dyn_threshold = 0.92
                    elif not same_source:
                        dyn_threshold = 0.78
                    else:
                        dyn_threshold = 0.85

                    qdrant_score = float(qdrant_best.get("score", 0.0))
                    if qdrant_score >= dyn_threshold:
                        _merge_into_existing(
                            existing,
                            expires_at=expires_at,
                            source_weight=source_weight,
                            sentiment=sentiment,
                        )
                        await session.flush()
                        return existing, False

        # ── Create new Memory ─────────────────────────────────────────
        mem = Memory(
            user_id=user.id,
            contact_id=contact_id,
            fact=fact,
            sentiment=sentiment,
            source=source,
            confidence=confidence,
            times_mentioned=1,
            message_id=message_id,
            is_active=is_active,
            cluster_topic=cluster_topic,
            embedding_hash=emb_hash,
            importance=importance if importance is not None else 0.5,
            decay_rate=decay_rate if decay_rate is not None else 0.07,
            memory_tier=memory_tier if memory_tier is not None else 1,
            memory_type=memory_type,
            pinned=pinned if pinned is not None else False,
            expires_at=expires_at,
            use_count=use_count if use_count is not None else 0,
            source_quality=source_quality if source_quality is not None else 0.5,
            extraction_quality=extraction_quality
            if extraction_quality is not None
            else 0.5,
        )
        session.add(mem)
        await session.flush()

    return mem, True


async def _delete_memory_core(
    session: AsyncSession, user: User, memory_id: int
) -> tuple[bool, int | None]:
    """Pure-DB soft-delete for a single Memory. NO side effects.

    Sets ``is_active = False`` and ``validity_end = now``.
    Returns ``(success, contact_id)`` — ``contact_id`` is the
    deleted memory's contact (or ``None``), so the caller can
    invalidate the contact digest.

    Cache invalidation, recall-version bump, and digest invalidation
    are the caller's responsibility (see
    :func:`src.core.memory.memory_service.delete_memory_service`).

    Acquires the per-user lock to prevent races with concurrent
    ``_add_memory_core`` (which merges into active memories).
    """
    from src.db.repos.session_repo import _get_user_lock

    lock = _get_user_lock(user.id)
    async with lock:
        m = await session.get(Memory, memory_id)
        if m is None or m.user_id != user.id:
            return False, None
        # Soft delete — данные не удаляются безвозвратно
        m.is_active = False
        m.validity_end = datetime.now(UTC)
        await session.flush()
        return True, m.contact_id


async def _bulk_delete_memory_core(
    session: AsyncSession, user: User, memory_ids: list[int]
) -> tuple[list[int], list[int]]:
    """Pure-DB batch soft-delete for a list of Memory IDs.

    Returns (deleted_ids, contact_ids) for side effects in the caller.
    Acquires the per-user lock once for the whole batch.
    """
    from src.db.repos.session_repo import _get_user_lock

    lock = _get_user_lock(user.id)
    deleted_ids: list[int] = []
    contact_ids: list[int] = []
    now = datetime.now(UTC)
    async with lock:
        for memory_id in memory_ids:
            m = await session.get(Memory, memory_id)
            if m is None or m.user_id != user.id or not m.is_active:
                continue
            m.is_active = False
            m.validity_end = now
            deleted_ids.append(memory_id)
            if m.contact_id is not None:
                contact_ids.append(m.contact_id)
        if deleted_ids:
            await session.flush()
    return deleted_ids, contact_ids


async def add_memories(
    session: AsyncSession,
    user: User,
    facts: list[dict],
    *,
    source: str = "auto",
    contact_id: int | None = None,
) -> int:
    """Добавляет несколько фактов в память с batch-дедупликацией (один flush).

    В отличие от _add_memory_core (single-fact), НЕ выполняет:
      - Qdrant семантическую дедупликацию (только SHA256 хеш)
      - Auto-linking (batch-линковка сложна и избыточна для auto-save)
      - Хуки on_memory_saved (опциональны)
      - Qdrant индексацию эмбеддингов

    Использует один flush на всю пачку — убирает N+1 для автопамяти.
    Инвалидация кэша и bump_recall_version выполняются вызывающим
    (например, src.core.memory.memory_service.save_memories_batch).

    Returns: количество сохранённых/обновлённых фактов.
    """
    from src.db.repos.session_repo import _get_user_lock

    # ── Step 0: Filter & normalize each fact dict ─────────────────────
    source_weight_val = _SOURCE_WEIGHT_MAP.get(source, 0.3)

    # dedup within batch by hash; last write wins for same hash
    # (text, sentiment, has_temporal_marker)
    batch_deduped: dict[str, tuple[str, str, bool, dict[str, object]]] = {}
    for f in facts:
        fact_text = f.get("fact", "").strip()
        if not fact_text or len(fact_text) < 5:
            continue
        sentiment = f.get("sentiment", "neutral")
        if sentiment not in ("positive", "negative", "neutral"):
            sentiment = "neutral"
        emb_hash = hashlib.sha256(fact_text.lower().strip().encode()).hexdigest()[:16]
        has_temp = bool(_TEMPORAL_RE.search(fact_text))
        meta = {
            k: f[k]
            for k in (
                "contact_id",
                "confidence",
                "importance",
                "decay_rate",
                "memory_tier",
                "memory_type",
                "source_quality",
                "extraction_quality",
            )
            if k in f
        }
        batch_deduped[emb_hash] = (fact_text, sentiment, has_temp, meta)

    if not batch_deduped:
        return 0

    hashes = list(batch_deduped.keys())

    lock = _get_user_lock(user.id)

    async with lock:
        # ── Step 1: ONE query for all existing active memories ────────
        result = await session.execute(
            select(Memory).where(
                Memory.user_id == user.id,
                Memory.embedding_hash.in_(hashes),
                Memory.is_active.is_(True),
            )
        )
        existing_map: dict[str | None, Memory] = {
            m.embedding_hash: m for m in result.scalars().all()
        }

        new_memories: list[Memory] = []
        stored = 0

        for emb_hash, (fact_text, sentiment, has_temp, meta) in batch_deduped.items():
            existing = existing_map.get(emb_hash)

            if existing is not None and not has_temp:
                # Update existing duplicate (single source of truth for merge)
                _merge_into_existing(
                    existing,
                    expires_at=None,
                    source_weight=source_weight_val,
                    sentiment=sentiment,
                )
                stored += 1
            else:
                # Create new Memory
                mem = Memory(
                    user_id=user.id,
                    contact_id=meta.get("contact_id", contact_id),
                    fact=fact_text,
                    sentiment=sentiment,
                    source=source,
                    confidence=meta.get("confidence", 0.5),
                    times_mentioned=1,
                    is_active=True,
                    embedding_hash=emb_hash,
                    importance=meta.get("importance", 0.5),
                    decay_rate=meta.get("decay_rate", 0.07),
                    memory_tier=meta.get("memory_tier", 1),
                    memory_type=meta.get("memory_type"),
                    source_quality=meta.get("source_quality", 0.5),
                    extraction_quality=meta.get("extraction_quality", 0.5),
                )
                new_memories.append(mem)
                stored += 1

        if new_memories:
            session.add_all(new_memories)

        if stored:
            await session.flush()

    logger.debug(
        "add_memories batch: %d facts stored (source=%s, user=%d)",
        stored,
        source,
        user.id,
    )
    return stored


async def batch_link_memories(
    session: AsyncSession,
    user: User,
    pending_links: list[tuple[int, int, float, str | None]],
) -> int:
    """Batch-create/update MemoryLinks. Returns count of links created/updated.

    # Batch query optimization — replaces N individual link_memories() calls
    # with 3 queries: ownership check, existing check, batch insert.
    """
    if not pending_links:
        return 0

    # De-duplicate by (source, target) pairs
    seen: set[tuple[int, int]] = set()
    unique_links: list[tuple[int, int, float, str | None]] = []
    for src, tgt, w, rt in pending_links:
        key = (src, tgt)
        if key not in seen:
            seen.add(key)
            unique_links.append((src, tgt, w, rt))

    if not unique_links:
        return 0

    # Collect all memory IDs involved
    all_ids: set[int] = set()
    for src, tgt, _, _ in unique_links:
        all_ids.add(src)
        all_ids.add(tgt)

    # Batch verify ownership — 1 query instead of N
    valid_ids = set(
        (
            await session.execute(
                select(Memory.id).where(
                    Memory.id.in_(list(all_ids)), Memory.user_id == user.id
                )
            )
        )
        .scalars()
        .all()
    )

    if not valid_ids:
        return 0

    # Filter to only valid pairs (both source and target must belong to user)
    valid_links = [
        (src, tgt, w, rt)
        for src, tgt, w, rt in unique_links
        if src in valid_ids and tgt in valid_ids
    ]
    if not valid_links:
        return 0

    # Batch check existing links — 1 query instead of 2*N
    src_ids = [link[0] for link in valid_links]
    tgt_ids = [link[1] for link in valid_links]
    existing_result = await session.execute(
        select(MemoryLink.source_id, MemoryLink.target_id).where(
            MemoryLink.user_id == user.id,
            MemoryLink.source_id.in_(src_ids),
            MemoryLink.target_id.in_(tgt_ids),
        )
    )
    existing_pairs: set[tuple[int, int]] = {(r[0], r[1]) for r in existing_result.all()}

    # Build links to create/update
    count = 0
    updates_to_apply: list[tuple[int, int, float, str | None]] = []
    for src, tgt, w, rt in valid_links:
        if (src, tgt) in existing_pairs:
            updates_to_apply.append((src, tgt, w, rt))
        else:
            # Create new forward link
            link = MemoryLink(
                user_id=user.id,
                source_id=src,
                target_id=tgt,
                weight=w,
                relation_type=rt,
            )
            session.add(link)

            # Create reverse link if not exists
            if (tgt, src) not in existing_pairs:
                rev = MemoryLink(
                    user_id=user.id,
                    source_id=tgt,
                    target_id=src,
                    weight=w,
                    relation_type=rt,
                )
                session.add(rev)
                existing_pairs.add(
                    (tgt, src)
                )  # prevent duplicate if (B,A) in pending_links
                count += 1

    if updates_to_apply:
        weight_cases = case(
            *[
                (
                    (MemoryLink.source_id == src) & (MemoryLink.target_id == tgt),
                    w,
                )
                for src, tgt, w, _rt in updates_to_apply
            ],
            else_=MemoryLink.weight,
        )

        rt_cases = case(
            *[
                (
                    (MemoryLink.source_id == src) & (MemoryLink.target_id == tgt),
                    rt if rt else MemoryLink.relation_type,
                )
                for src, tgt, _w, rt in updates_to_apply
            ],
            else_=MemoryLink.relation_type,
        )

        await session.execute(
            update(MemoryLink)
            .where(
                MemoryLink.user_id == user.id,
                or_(
                    *[
                        (MemoryLink.source_id == src) & (MemoryLink.target_id == tgt)
                        for src, tgt, _w, _rt in updates_to_apply
                    ]
                ),
            )
            .values(weight=weight_cases, relation_type=rt_cases)
        )

    if count or updates_to_apply:
        await session.flush()

    return count
