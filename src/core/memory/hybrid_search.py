"""Hybrid search: Reciprocal Rank Fusion (RRF) for keyword + vector results.

Combines BM25/FTS5 keyword results with cosine similarity vector results
using the RRF formula from Cormack et al., SIGIR 2009.

score(d) = sum_{r in rankings} 1 / (k + rank(d, r))

where:
- rank(d, r) is the position (1-indexed) of document d in ranking r
- k = 60 is the smoothing constant

Also provides :func:`search_memories_hybrid` — a high-level convenience that
runs FTS5 + vector search + RRF fusion in one call.  Used by both
:mod:`agent_dispatcher` and :mod:`memory_recall` (Issue 4 dedup fix).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.config import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from src.db.models import User

logger = logging.getLogger(__name__)

# RRF smoothing constant — standard value from the literature (Cormack et al. 2009)
RRF_K: int = settings.recall_rrf_k


def reciprocal_rank_fusion(
    vector_results: list[tuple[int, float]] | None = None,
    keyword_results: list[tuple[int, float]] | None = None,
    graph_results: list[tuple[int, float]] | None = None,
    *,
    k: int = RRF_K,
) -> list[tuple[int, float]]:
    """Combine two or three ranked result lists via Reciprocal Rank Fusion.

    Each input is a list of (id, score) tuples, sorted by relevance
    (best first). The score is used as a weight multiplier in RRF so that
    higher-relevance results contribute more, even at the same position.

    Args:
        vector_results: Ranked list of (memory_id, cosine_score) from Qdrant.
        keyword_results: Ranked list of (memory_id, bm25_score) from FTS5.
        graph_results: Ranked list of (memory_id, proximity_score) from BFS graph.
        k: RRF smoothing constant (default 60).

    Returns:
        List of (memory_id, fused_rrf_score) sorted by score descending.
    """
    scores: dict[int, float] = {}

    for ranking in (vector_results, keyword_results, graph_results):
        if not ranking:
            continue
        for rank_i, (mem_id, score) in enumerate(ranking, start=1):
            rrf_contrib = 1.0 / (k + rank_i)
            scores[mem_id] = scores.get(mem_id, 0.0) + rrf_contrib

    # Sort by fused score descending
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


# ── High-level hybrid entry point (Issue 4 dedup) ──────────────────


async def search_memories_hybrid(
    session,
    owner,
    query: str,
    *,
    limit: int = 10,
    threshold: float = 0.5,
    contact_id: int | None = None,
    graph_results: list[tuple[int, float]] | None = None,
) -> list[tuple[int, float]]:
    """Hybrid search: FTS5 + vector + optional graph → RRF fusion → (id, score) sorted desc.

    Graceful fallback: FTS5-only if vector/embedding unavailable.

    Args:
        session: SQLAlchemy AsyncSession.
        owner: User ORM instance (must have ``.id`` attribute).
        query: Natural-language search query.
        limit: Max results per branch before fusion.
        threshold: Minimum cosine similarity for vector candidate inclusion.
        contact_id: Optional contact filter (vector search only).
        graph_results: Optional ranked list of (memory_id, proximity_score) from
            BFS graph expansion for three-way RRF.

    Returns:
        List of ``(memory_id, score)`` tuples sorted by RRF score descending.
    """
    if not query or not query.strip():
        return []

    # 1. FTS5 keyword search
    from src.db.repo import search_memories_fts_with_scores

    fts_scores = await search_memories_fts_with_scores(
        session,
        owner,
        query,
        limit=limit,
    )

    # 2. Vector search (graceful fallback)
    vector_hits: list[tuple[int, float]] = []
    try:
        from src.core.actions.vector_store import get_vector_store
        from src.llm.router import build_provider
        from src.llm.base import TaskType

        provider = await build_provider(session, owner, task_type=TaskType.MEMORY)
        if provider:
            embedding = await provider.embed(query[:300])
            if embedding:
                vs = await get_vector_store()
                raw = await vs.search_similar_memories(
                    user_id=owner.id,
                    embedding=embedding,
                    threshold=threshold,
                    limit=limit,
                    contact_id=contact_id,
                )
                for h in raw:
                    mid = h.get("memory_id")
                    if mid is not None:
                        vector_hits.append((int(mid), h.get("score", 0.0)))
    except Exception:
        logger.debug("Vector search unavailable, FTS5-only", exc_info=True)

    # 3. RRF fusion — three-way when graph_results present; two-way otherwise
    if graph_results:
        return reciprocal_rank_fusion(
            vector_results=vector_hits or None,
            keyword_results=fts_scores or None,
            graph_results=graph_results,
        )[:limit]
    if vector_hits and fts_scores:
        return reciprocal_rank_fusion(
            vector_results=vector_hits,
            keyword_results=fts_scores,
        )[:limit]
    if vector_hits:
        return sorted(vector_hits, key=lambda x: x[1], reverse=True)[:limit]
    return [(mid, score) for mid, score in fts_scores[:limit]]


__all__ = [
    "RRF_K",
    "reciprocal_rank_fusion",
]
