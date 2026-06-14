"""Memory repository — backward-compatible re-export facade.

All implementation in _core.py. This package exists so future splits
(_fts.py, _crud.py, _graph.py etc.) can be imported here without
breaking existing callers.
"""

from src.db.repos.memory_repo._core import (
    # ── Types ──────────────────────────────────────────────────────
    ContactImpact,
    FtsHit,
    # ── FTS search ─────────────────────────────────────────────────
    cross_chat_search,
    fts_search,
    # ── CRUD ───────────────────────────────────────────────────────
    add_memory,
    add_memory_candidate,
    delete_memory,
    delete_memory_candidate,
    list_memories,
    list_memory_candidates,
    # ── Search / similarity ────────────────────────────────────────
    find_similar_memories,
    get_memory_stats,
    search_memories,
    search_memories_fts,
    search_memories_fts_with_scores,
    # ── Clusters ───────────────────────────────────────────────────
    add_member,
    get_cluster_members,
    list_clusters_for_contact,
    list_memory_clusters,
    upsert_memory_cluster,
    # ── Graph / links ──────────────────────────────────────────────
    get_graph_stats,
    get_linked_memories,
    get_memory_graph,
    link_memories,
    unlink_memories,
    # ── Impact ─────────────────────────────────────────────────────
    contact_impact,
    # ── Versioning ─────────────────────────────────────────────────
    get_memory_history,
    rollback_memory,
    save_memory_version,
)

__all__ = [
    "ContactImpact",
    "FtsHit",
    "add_member",
    "add_memory",
    "add_memory_candidate",
    "contact_impact",
    "cross_chat_search",
    "delete_memory",
    "delete_memory_candidate",
    "find_similar_memories",
    "fts_search",
    "get_cluster_members",
    "get_graph_stats",
    "get_linked_memories",
    "get_memory_graph",
    "get_memory_history",
    "get_memory_stats",
    "link_memories",
    "list_clusters_for_contact",
    "list_memories",
    "list_memory_candidates",
    "list_memory_clusters",
    "rollback_memory",
    "save_memory_version",
    "search_memories",
    "search_memories_fts",
    "search_memories_fts_with_scores",
    "unlink_memories",
    "upsert_memory_cluster",
]
