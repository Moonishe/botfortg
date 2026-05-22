"""LRU-кэш эмбеддингов с двухуровневым хранением:
L1 (in-memory OrderedDict) — быстрый доступ,
L2 (SQLite) — персистентность между перезапусками.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path

from src.config import settings

logger = logging.getLogger(__name__)

_cache: OrderedDict[str, list[float]] = OrderedDict()
_lock = threading.Lock()
MAX_SIZE = 500

# ── SQLite connection (lazy) ────────────────────────────────────────────────

_sqlite_conn: sqlite3.Connection | None = None
_sqlite_lock = threading.Lock()


def _get_db_path() -> Path:
    """Extract the filesystem path from ``database_url``.

    Handles ``sqlite+aiosqlite:///path`` and bare ``sqlite:///path``.
    Falls back to ``data_dir / "app.db"`` if parsing fails or URL
    is an in-memory database (``:memory:``).
    """
    raw = settings.database_url
    # Strip scheme prefixes: sqlite+aiosqlite:/// or sqlite:///
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if raw.startswith(prefix):
            path_part = raw.removeprefix(prefix)
            # In-memory database — fall back to file-based cache
            if path_part in (":memory:", ""):
                break
            return settings.data_dir / Path(path_part).name
    return settings.data_dir / "app.db"


def _get_conn() -> sqlite3.Connection:
    """Return a thread-safe SQLite connection (created once, reused)."""
    global _sqlite_conn
    if _sqlite_conn is None:
        with _sqlite_lock:
            if _sqlite_conn is None:
                db_path = _get_db_path()
                db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(
                    str(db_path),
                    timeout=30,
                    check_same_thread=False,  # guarded by _lock
                )
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS embedding_cache (
                        text_hash TEXT PRIMARY KEY,
                        model TEXT NOT NULL DEFAULT '',
                        embedding_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        last_accessed_at TEXT NOT NULL
                    )"""
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_embedding_cache_text_hash "
                    "ON embedding_cache(text_hash)"
                )
                conn.commit()
                _sqlite_conn = conn
    return _sqlite_conn


# ── JSON helpers ────────────────────────────────────────────────────────────


def _serialize_embedding(vec: list[float]) -> str:
    return json.dumps(vec, ensure_ascii=False)


def _deserialize_embedding(s: str) -> list[float]:
    return json.loads(s)


# ── Hashing ─────────────────────────────────────────────────────────────────


def _hash(text: str, model: str = "") -> str:
    raw = f"{model}||{text}" if model else text
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Public API ──────────────────────────────────────────────────────────────


def get(text: str, model: str = "") -> list[float] | None:
    """Return cached embedding if present (L1 → L2)."""
    key = _hash(text, model)

    # L1 fast path
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]

    # L2 — lazy load from SQLite
    try:
        conn = _get_conn()
        row: tuple[str, str] | None = None
        with _sqlite_lock:
            row = conn.execute(
                "SELECT embedding_json FROM embedding_cache WHERE text_hash = ? AND model = ?",
                (key, model),
            ).fetchone()
        if row is not None:
            embedding = _deserialize_embedding(row[0])
            # Promote to L1
            with _lock:
                if key not in _cache:
                    if len(_cache) >= MAX_SIZE:
                        _cache.popitem(last=False)
                _cache[key] = embedding
                _cache.move_to_end(key)
            # Touch last_accessed_at (fire-and-forget, non-critical)
            try:
                with _sqlite_lock:
                    conn.execute(
                        "UPDATE embedding_cache SET last_accessed_at = datetime('now') "
                        "WHERE text_hash = ?",
                        (key,),
                    )
                    conn.commit()
            except Exception:
                pass
            return embedding
    except Exception:
        logger.warning("SQLite read failed for embedding cache", exc_info=True)

    return None


def set(text: str, embedding: list[float], model: str = "") -> None:
    """Store embedding in L1 and persist to SQLite (fire-and-forget)."""
    key = _hash(text, model)

    # L1 write
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            _cache[key] = embedding
        else:
            if len(_cache) >= MAX_SIZE:
                _cache.popitem(last=False)
            _cache[key] = embedding

    # L2 — persist to SQLite (best-effort, non-blocking for caller)
    try:
        conn = _get_conn()
        emb_json = _serialize_embedding(embedding)
        with _sqlite_lock:
            conn.execute(
                """INSERT OR REPLACE INTO embedding_cache (text_hash, model, embedding_json, created_at, last_accessed_at)
                   VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
                (key, model, emb_json),
            )
            conn.commit()
    except Exception:
        logger.warning("SQLite write failed for embedding cache", exc_info=True)
        # L1 entry is kept — no data loss for the current session


def clear() -> None:
    """Clear both L1 in-memory cache and SQLite table."""
    with _lock:
        _cache.clear()
    try:
        conn = _get_conn()
        with _sqlite_lock:
            conn.execute("DELETE FROM embedding_cache")
            conn.commit()
    except Exception:
        logger.warning("SQLite clear failed for embedding cache", exc_info=True)


def size() -> int:
    """Return the number of entries in the in-memory cache."""
    with _lock:
        return len(_cache)
