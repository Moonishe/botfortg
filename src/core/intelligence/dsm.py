"""DSM — Design-Structured Memory. Cross-session project memory."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, UTC

from src.config import settings
from src.core.infra.sqlite_persistent import (
    PersistentSQLite,
    get_db_path,
    migrate_from_app_db,
)

logger = logging.getLogger(__name__)


def _migrate_dsm_from_app_db(conn: sqlite3.Connection) -> None:
    """Copy legacy dsm_entries from app.db if the new dedicated DB is empty."""
    migrate_from_app_db(
        conn,
        table_name="dsm_entries",
        columns=[
            "key",
            "content",
            "tags",
            "source",
            "importance",
            "created_at",
            "accessed_at",
        ],
        old_db_path=get_db_path(),
        log_label="DSM",
    )


# In-memory cache of recent entries for fast session-start injection.
_DSM_CACHE: list[dict] = []
_DSM_CACHE_TTL: float = 300  # 5 min
_DSM_CACHE_TS: float = 0
_DSM_CACHE_LOCK = asyncio.Lock()

# Persistent SQLite connection managed by the shared infra helper.
_dsm_db = PersistentSQLite(
    db_path=settings.data_dir / "dsm.db",
    table_ddl="""
        CREATE TABLE IF NOT EXISTS dsm_entries (
            key TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            importance REAL NOT NULL DEFAULT 0.5,
            created_at TEXT NOT NULL,
            accessed_at TEXT NOT NULL
        )
    """,
    extra_sql=[
        "CREATE INDEX IF NOT EXISTS ix_dsm_tags ON dsm_entries(tags)",
        "CREATE INDEX IF NOT EXISTS ix_dsm_created ON dsm_entries(created_at)",
    ],
    init_fn=_migrate_dsm_from_app_db,
)


def _escape_like(pattern: str) -> str:
    """Escape LIKE wildcards so user input cannot match every row."""
    return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def dsm_search(query: str, limit: int = 5) -> list[dict]:
    """Search DSM via substring match + ranking.

    Returns [{key, content, tags, importance, created_at}].
    """
    # ponytail: empty query → no matches (LIKE '%%' would return everything)
    if not query or not query.strip():
        return []
    limit = max(1, limit)
    try:
        escaped = _escape_like(query)
        patterns = (f"%{escaped}%", f"%{escaped}%", f"%{escaped}%", limit)

        def _do() -> list[tuple]:
            with _dsm_db.locked() as conn:
                return conn.execute(
                    "SELECT key, content, tags, importance, created_at "
                    "FROM dsm_entries "
                    "WHERE content LIKE ? ESCAPE '\\' "
                    "OR tags LIKE ? ESCAPE '\\' "
                    "OR key LIKE ? ESCAPE '\\' "
                    "ORDER BY importance DESC, created_at DESC LIMIT ?",
                    patterns,
                ).fetchall()

        rows = await asyncio.to_thread(_do)
        return [
            {
                "key": r[0],
                "content": r[1],
                "tags": r[2],
                "importance": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]
    except Exception:
        logger.debug("DSM search failed", exc_info=True)
        return []


async def dsm_get_recent(days: int = 7, limit: int = 10) -> list[dict]:
    """Load recent DSM entries for session start injection.

    Returns [{key, content, tags, importance}].
    """
    if days < 1:
        raise ValueError(f"days must be >= 1, got {days}")
    limit = max(1, limit)
    global _DSM_CACHE, _DSM_CACHE_TS
    async with _DSM_CACHE_LOCK:
        now_ts = asyncio.get_running_loop().time()
        if _DSM_CACHE and (now_ts - _DSM_CACHE_TS) < _DSM_CACHE_TTL:
            return _DSM_CACHE[:limit]
    rows = await _fetch_recent_dsm_rows(days, limit * 2)
    async with _DSM_CACHE_LOCK:
        # Double-check: another coroutine may have populated the cache
        # while we were awaiting the DB fetch.
        now_ts = asyncio.get_running_loop().time()
        if _DSM_CACHE and (now_ts - _DSM_CACHE_TS) < _DSM_CACHE_TTL:
            return _DSM_CACHE[:limit]
        _DSM_CACHE = [
            {"key": r[0], "content": r[1], "tags": r[2], "importance": r[3]}
            for r in rows
        ]
        _DSM_CACHE_TS = now_ts
        return _DSM_CACHE[:limit]


async def _fetch_recent_dsm_rows(days: int, limit: int) -> list[tuple]:
    """Fetch recent rows from the DSM DB. Returns raw rows on success, [] on failure."""
    try:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

        def _do() -> list[tuple]:
            with _dsm_db.locked() as conn:
                return conn.execute(
                    "SELECT key, content, tags, importance FROM dsm_entries "
                    "WHERE created_at >= ? "
                    "ORDER BY importance DESC, created_at DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()

        return await asyncio.to_thread(_do)
    except Exception:
        logger.debug("DSM get_recent fetch failed", exc_info=True)
        return []


async def dsm_list_tags() -> list[str]:
    """All unique tags."""
    try:

        def _do() -> list[tuple]:
            with _dsm_db.locked() as conn:
                return conn.execute(
                    "SELECT DISTINCT tags FROM dsm_entries WHERE tags != ''"
                ).fetchall()

        rows = await asyncio.to_thread(_do)
        tags: set[str] = set()
        for r in rows:
            for t in r[0].split(","):
                t = t.strip()
                if t:
                    tags.add(t)
        return sorted(tags)
    except Exception:
        logger.debug("dsm_list_tags failed", exc_info=True)
        return []


async def dsm_cleanup(days: int = 30) -> int:
    """Delete entries older than N days. Returns count."""
    if days < 1:
        raise ValueError(f"days must be >= 1, got {days}")
    try:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

        def _do() -> int:
            with _dsm_db.locked() as conn:
                res = conn.execute(
                    "DELETE FROM dsm_entries WHERE created_at < ?", (cutoff,)
                )
                conn.commit()
                return res.rowcount

        return await asyncio.to_thread(_do)
    except Exception:
        logger.debug("dsm_cleanup failed", exc_info=True)
        return 0


async def close_dsm_db() -> None:
    """Close the persistent DSM SQLite connection (graceful shutdown)."""
    await asyncio.to_thread(_dsm_db.close)
    logger.debug("DSM database connection closed")
