"""Core auto-linking logic for a newly saved memory fact.

This module lives in the core layer because it orchestrates DB reads and the
Qdrant vector store (both downstream dependencies). It used to live inside
``src.db.repos.memory_repo._graph`` which made the DB layer depend on core
components, breaking the DB -> Core dependency direction.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.actions.vector_store import get_vector_store
from src.core.memory.relation_types import RelationType
from src.db.models import Memory, User
from src.db.repo import batch_link_memories

logger = logging.getLogger(__name__)

# Removes all non-word characters (punctuation, spaces, etc.) from a token.
_TOKEN_PUNCT_RE = re.compile(r"\W+")


def _clean_token(word: str) -> str:
    return _TOKEN_PUNCT_RE.sub("", word)


async def auto_link_memory(
    session: AsyncSession,
    user: User,
    memory: Memory,
    embedding: list[float] | None = None,
) -> None:
    """Auto-link new fact to related facts via multiple strategies.

    Primary: Qdrant cosine similarity → "supports" / "related".
    Fallback: keyword overlap → "related".
    Supplementary passes (always run):
      - Temporal co-occurrence (same contact, <1h apart) → "co_temporal"
      - Entity co-occurrence (shared proper nouns) → "co_entity"
      - Cause-effect hint (positive→negative same contact) → "preceded"

    All supplementary passes add links on top of the existing ones.
    """
    if not memory.fact or not memory.is_active:
        return

    # Collect all pending links, flush once at the end
    pending_links: list[tuple[int, int, float, str | None]] = []

    # ── Pass 1: Semantic linking via Qdrant ──────────────────────────────
    if embedding:
        try:
            similar = await (await get_vector_store()).search_similar_memories(
                user_id=user.id,
                embedding=embedding,
                threshold=0.65,  # lower than dedup (0.85)
                limit=10,
                contact_id=None,  # search across all contacts for cross-links
            )

            for hit in similar:
                hit_id = hit.get("memory_id")
                if hit_id is None or hit_id == memory.id:
                    continue

                cosine_score = hit.get("score", 0.0)
                if cosine_score < 0.65:
                    continue

                if cosine_score >= 0.90:
                    relation_type = RelationType.SUPPORTS
                elif cosine_score >= 0.75:
                    relation_type = RelationType.RELATED  # strong
                else:
                    relation_type = RelationType.RELATED  # weak

                pending_links.append((memory.id, hit_id, cosine_score, relation_type))
        except Exception:  # ponytail: Qdrant-specific exceptions are not exported
            # При ошибке fallback на keyword overlap.
            logger.debug(
                "Semantic linking failed, falling back to keyword overlap",
                exc_info=True,
            )

    # ── Pass 2: Keyword overlap fallback (only if no semantic links) ─────
    if len(pending_links) == 0:
        words = {w.lower() for w in memory.fact.split() if len(w) >= 4}
        if len(words) >= 2:
            candidates_q = (
                select(Memory)
                .where(
                    Memory.user_id == user.id,
                    Memory.is_active.is_(True),
                    Memory.id != memory.id,
                    Memory.contact_id == memory.contact_id,
                )
                .limit(30)
            )
            result = await session.execute(candidates_q)
            candidates = result.scalars().all()

            for c in candidates:
                if not c.fact:
                    continue
                c_words = {w.lower() for w in c.fact.split() if len(w) >= 4}
                overlap = len(words & c_words)
                if overlap >= 2:
                    weight = 0.3 + overlap * 0.1
                    pending_links.append(
                        (memory.id, c.id, weight, RelationType.RELATED)
                    )

    # ── Pass 3: Temporal co-occurrence ───────────────────────────────────
    # Same contact, created_at within 1 hour
    if memory.contact_id is not None and memory.created_at is not None:
        window_start = memory.created_at - timedelta(hours=1)
        window_end = memory.created_at + timedelta(hours=1)
        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == user.id,
                Memory.is_active.is_(True),
                Memory.id != memory.id,
                Memory.contact_id == memory.contact_id,
                Memory.created_at.between(window_start, window_end),
            )
            .limit(30)
        )
        for c in result.scalars().all():
            if not c.fact:
                continue
            pending_links.append((memory.id, c.id, 0.5, RelationType.CO_TEMPORAL))

    # ── Pass 4: Entity co-occurrence (shared proper nouns) ───────────────
    # Simple: capitalized word >= 3 chars
    proper_nouns: set[str] = set()
    for word in memory.fact.split():
        clean = _clean_token(word)
        if len(clean) >= 3 and clean[0].isupper() and clean.isalpha():
            proper_nouns.add(clean)
    if proper_nouns:
        conditions = [Memory.fact.ilike(f"%{pn}%") for pn in proper_nouns]
        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == user.id,
                Memory.is_active.is_(True),
                Memory.id != memory.id,
                or_(*conditions),
            )
            .limit(30)
        )
        for c in result.scalars().all():
            if not c.fact:
                continue
            # Double-check: does c.fact actually contain any of the same proper nouns?
            c_upper = {
                token
                for token in map(_clean_token, c.fact.split())
                if len(token) >= 3 and token[0].isupper() and token.isalpha()
            }
            if proper_nouns & c_upper:
                pending_links.append((memory.id, c.id, 0.4, RelationType.CO_ENTITY))

    # ── Pass 5: Cause-effect hint ────────────────────────────────────────
    # If new fact is negative, link from older positive facts of same contact
    if (
        memory.sentiment == "negative"
        and memory.contact_id is not None
        and memory.created_at is not None
    ):
        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == user.id,
                Memory.is_active.is_(True),
                Memory.id != memory.id,
                Memory.contact_id == memory.contact_id,
                Memory.sentiment == "positive",
                Memory.created_at < memory.created_at,
            )
            .limit(10)
        )
        for c in result.scalars().all():
            if not c.fact:
                continue
            pending_links.append((c.id, memory.id, 0.3, RelationType.PRECEDED))

    # ── Batch flush all links ────────────────────────────────────────────
    if pending_links:
        links_added = await batch_link_memories(session, user, pending_links)
        if links_added:
            logger.debug(
                "Auto-linked %d facts to memory %d",
                links_added,
                memory.id,
            )
