"""DSM — Design-Structured Memory. Cross-session project memory."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, UTC

from src.config import settings
from src.core.memory.context_files import _get_db_path

logger = logging.getLogger(__name__)

_DSM_CACHE: list[dict] = []  # in-memory cache of recent entries
_DSM_CACHE_TTL: float = 300  # 5 min
_DSM_CACHE_TS: float = 0
_DSM_CACHE_LOCK = asyncio.Lock()
_DSM_MIGRATED = False
_DSM_MIGRATION_LOCK = threading.Lock()


def _get_dsm_db() -> sqlite3.Connection:
    db_path = settings.data_dir / "dsm.db"
    # check_same_thread=False: соединение передаётся между потоками через
    # asyncio.to_thread (executor назначает любой worker-поток из пула).
    # Без этого флага SQLite выбрасывает ProgrammingError при переходе
    # соединения в другой поток. WAL-режим обеспечивает безопасность.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dsm_entries (
            key TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            importance REAL NOT NULL DEFAULT 0.5,
            created_at TEXT NOT NULL,
            accessed_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_dsm_tags ON dsm_entries(tags)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_dsm_created ON dsm_entries(created_at)")
    conn.commit()
    _maybe_migrate_dsm(conn)
    return conn


def _maybe_migrate_dsm(conn: sqlite3.Connection) -> None:
    """Copy legacy dsm_entries from app.db if the new dedicated DB is empty."""
    global _DSM_MIGRATED
    with _DSM_MIGRATION_LOCK:
        if _DSM_MIGRATED:
            return
        try:
            row = conn.execute("SELECT COUNT(*) FROM dsm_entries").fetchone()
            if row and row[0] > 0:
                _DSM_MIGRATED = True
                return
        except sqlite3.OperationalError:
            _DSM_MIGRATED = True
            return
        old_db_path = _get_db_path()
        if not old_db_path.exists():
            _DSM_MIGRATED = True
            return
        try:
            with sqlite3.connect(str(old_db_path)) as old_conn:
                old_conn.execute("PRAGMA busy_timeout=30000")
                old_rows = old_conn.execute(
                    """
                    SELECT key, content, tags, source, importance,  # noqa: E501
                        created_at, accessed_at
                    FROM dsm_entries
                    """
                ).fetchall()
            if old_rows:
                conn.executemany(
                    """
                    INSERT INTO dsm_entries(
                        key, content, tags, source, importance, created_at, accessed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    old_rows,
                )
                conn.commit()
                logger.info(
                    "Migrated %d DSM entries from %s to %s",
                    len(old_rows),
                    old_db_path,
                    conn.execute("PRAGMA database_list").fetchone()[2],
                )
        except Exception:
            logger.debug("DSM migration not possible", exc_info=True)
        finally:
            _DSM_MIGRATED = True


async def dsm_write(
    key: str, content: str, *, tags: str = "", source: str = "", importance: float = 0.5
) -> bool:
    """Write a fact/decision to DSM. Deduplicates by key (overwrites if exists)."""
    conn = None
    try:
        conn = await asyncio.to_thread(_get_dsm_db)
        now = datetime.now(UTC).isoformat()
        await asyncio.to_thread(
            lambda: conn.execute(
                "INSERT OR REPLACE INTO dsm_entries("
                "key, content, tags, source, importance, created_at, accessed_at"
                ") VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM dsm_entries WHERE key=?), ?), ?)",  # noqa: E501
                (key, content[:2000], tags, source, importance, key, now, now),
            )
        )
        await asyncio.to_thread(conn.commit)
        logger.debug("DSM write: %s", key)
        return True
    except Exception:
        logger.debug("DSM write failed: %s", key, exc_info=True)
        return False
    finally:
        if conn is not None:
            await asyncio.to_thread(conn.close)


async def dsm_search(query: str, limit: int = 5) -> list[dict]:
    """Search DSM via substring match + ranking.

    Returns [{key, content, tags, importance, created_at}].
    """
    conn = None
    try:
        conn = await asyncio.to_thread(_get_dsm_db)
        rows = await asyncio.to_thread(
            lambda: conn.execute(
                "SELECT key, content, tags, importance, created_at "
                "FROM dsm_entries "
                "WHERE content LIKE ? OR tags LIKE ? OR key LIKE ? "
                "ORDER BY importance DESC, created_at DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        )
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
    finally:
        if conn is not None:
            await asyncio.to_thread(conn.close)


async def dsm_get_recent(days: int = 7, limit: int = 10) -> list[dict]:
    """Load recent DSM entries for session start injection."""
    global _DSM_CACHE, _DSM_CACHE_TS
    async with _DSM_CACHE_LOCK:
        now_ts = asyncio.get_running_loop().time()
        if _DSM_CACHE and (now_ts - _DSM_CACHE_TS) < _DSM_CACHE_TTL:
            return _DSM_CACHE[:limit]
    rows = await _fetch_recent_dsm_rows(days, limit * 2)
    async with _DSM_CACHE_LOCK:
        _DSM_CACHE = [
            {"key": r[0], "content": r[1], "tags": r[2], "importance": r[3]}
            for r in rows
        ]
        _DSM_CACHE_TS = asyncio.get_running_loop().time()
        return _DSM_CACHE[:limit]


async def _fetch_recent_dsm_rows(days: int, limit: int) -> list[tuple]:
    """Fetch recent rows from the DSM DB. Returns raw rows on success, [] on failure."""
    conn = None
    try:
        conn = await asyncio.to_thread(_get_dsm_db)
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        rows = await asyncio.to_thread(
            lambda: conn.execute(
                "SELECT key, content, tags, importance FROM dsm_entries "
                "WHERE created_at >= ? "
                "ORDER BY importance DESC, created_at DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        )
        return rows
    except Exception:
        logger.debug("DSM get_recent fetch failed", exc_info=True)
        return []
    finally:
        if conn is not None:
            await asyncio.to_thread(conn.close)


async def dsm_list_tags() -> list[str]:
    """All unique tags."""
    conn = None
    try:
        conn = await asyncio.to_thread(_get_dsm_db)
        rows = await asyncio.to_thread(
            lambda: conn.execute(
                "SELECT DISTINCT tags FROM dsm_entries WHERE tags != ''"
            ).fetchall()
        )
        tags = set()
        for r in rows:
            for t in r[0].split(","):
                t = t.strip()
                if t:
                    tags.add(t)
        return sorted(tags)
    except Exception:
        logger.debug("dsm_list_tags failed", exc_info=True)
        return []
    finally:
        if conn is not None:
            await asyncio.to_thread(conn.close)


async def dsm_cleanup(days: int = 30) -> int:
    """Delete entries older than N days. Returns count."""
    conn = None
    try:
        conn = await asyncio.to_thread(_get_dsm_db)
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        res = await asyncio.to_thread(
            lambda: conn.execute(
                "DELETE FROM dsm_entries WHERE created_at < ?", (cutoff,)
            )
        )
        await asyncio.to_thread(conn.commit)
        return res.rowcount if res else 0
    except Exception:
        logger.debug("dsm_cleanup failed", exc_info=True)
        return 0
    finally:
        if conn is not None:
            await asyncio.to_thread(conn.close)
