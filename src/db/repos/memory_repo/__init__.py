"""Memory repository — backward-compatible re-export facade.

All implementation split across focused modules:
  _core.py     — core CRUD (add/delete memory, batch ops)
  _fts.py      — FTS helpers and search
  _graph.py    — memory graph and auto-linking
  _clusters.py — memory clusters
  _candidates.py — memory candidates
  _versioning.py — memory versioning
  _impact.py   — impact analysis
  _search.py   — general search
"""

from src.db.repos.memory_repo._core import (
    # ── CRUD ───────────────────────────────────────────────────────
    add_memories,
    batch_link_memories,
)

from src.db.repos.memory_repo._fts import (
    # ── Types ──────────────────────────────────────────────────────
    FtsHit,
    # ── FTS search ─────────────────────────────────────────────────
    cross_chat_search,
    find_similar_memories,
    fts_search,
    search_memories_fts,
    search_memories_fts_with_scores,
)

from src.db.repos.memory_repo._graph import (
    # ── Graph / links ──────────────────────────────────────────────
    get_graph_stats,
    get_linked_memories,
    get_memory_graph,
    link_memories,
    list_memory_links,
    unlink_memories,
)

from src.db.repos.memory_repo._clusters import (
    # ── Clusters ───────────────────────────────────────────────────
    add_member,
    get_cluster_members,
    list_clusters_for_contact,
    list_memory_clusters,
    upsert_memory_cluster,
)

from src.db.repos.memory_repo._candidates import (
    # ── Candidates ─────────────────────────────────────────────────
    add_memory_candidate,
    delete_memory_candidate,
    list_memory_candidates,
)

from src.db.repos.memory_repo._versioning import (
    # ── Versioning ─────────────────────────────────────────────────
    get_memory_history,
    rollback_memory,
    save_memory_version,
)

from src.db.repos.memory_repo._impact import (
    # ── Impact ─────────────────────────────────────────────────────
    ContactImpact,
    contact_impact,
)

from src.db.repos.memory_repo._search import (
    # ── Search ─────────────────────────────────────────────────────
    list_memories,
    search_memories,
)

__all__ = [
    "ContactImpact",
    "FtsHit",
    "add_member",
    "add_memories",
    "add_memory_candidate",
    "batch_link_memories",
    "contact_impact",
    "cross_chat_search",
    "delete_memory_candidate",
    "find_similar_memories",
    "fts_search",
    "get_cluster_members",
    "get_graph_stats",
    "get_linked_memories",
    "get_memory_graph",
    "get_memory_history",
    "link_memories",
    "list_clusters_for_contact",
    "list_memories",
    "list_memory_candidates",
    "list_memory_clusters",
    "list_memory_links",
    "rollback_memory",
    "save_memory_version",
    "search_memories",
    "search_memories_fts",
    "search_memories_fts_with_scores",
    "unlink_memories",
    "upsert_memory_cluster",
]
