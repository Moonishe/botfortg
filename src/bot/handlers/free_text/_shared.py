"""Shared constants for free_text pipeline sub-modules.

Imported by _core.py, _dag.py, and _confirm.py.
Must remain dependency-free — no imports from _core, _dag, or _confirm.
"""

from src.config import settings

# ── Intent context TTL (seconds) ──────────────────────────────────────────
_LAST_INTENT_TTL: float = 900.0

# ── Pending confirmation TTL (seconds) — defaults to 300 if unconfigured ──
_PENDING_TTL: float = float(getattr(settings, "pending_ttl_sec", 300))

# ── Dedup cache limits ────────────────────────────────────────────────────
_DEDUP_CACHE_MAX: int = 200
_DEDUP_CACHE_TTL: float = 60.0  # seconds
