"""LRU-кэш эмбеддингов с двухуровневым хранением:
L1 (in-memory OrderedDict) — быстрый доступ,
L2 (SQLite via aiosqlite) — персистентность между перезапусками.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any

import aiosqlite

from src.config import settings

logger = logging.getLogger(__name__)

_cache: OrderedDict[str, list[float]] = OrderedDict()
_lock = asyncio.Lock()
MAX_SIZE = 500

# ── aiosqlite connection (lazy-init, persisted) ─────────────────────────────

_async_conn: aiosqlite.Connection | None = None
_conn_lock = asyncio.Lock()


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


async def _get_conn() -> aiosqlite.Connection:
    """Return a shared aiosqlite connection (created once, reused).

    Connects lazily on first call; thread-safe via double-checked
    locking with ``asyncio.Lock``.
    """
    global _async_conn
    if _async_conn is not None:
        return _async_conn
    async with _conn_lock:
        if _async_conn is not None:
            return _async_conn
        db_path = _get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(db_path))
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=30000")
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS embedding_cache (
                text_hash TEXT PRIMARY KEY,
                model TEXT NOT NULL DEFAULT '',
                embedding_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL
            )"""
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_embedding_cache_text_hash "
            "ON embedding_cache(text_hash)"
        )
        await conn.commit()
        _async_conn = conn
        return conn


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


async def get(text: str, model: str = "") -> list[float] | None:
    """Return cached embedding if present (L1 → L2)."""
    key = _hash(text, model)

    # L1 fast path
    async with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]

    # L2 — lazy load from aiosqlite
    try:
        conn = await _get_conn()
        async with conn.execute(
            "SELECT embedding_json FROM embedding_cache WHERE text_hash = ? AND model = ?",
            (key, model),
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None:
            embedding = _deserialize_embedding(row[0])
            # Promote to L1
            async with _lock:
                if key not in _cache:
                    if len(_cache) >= MAX_SIZE:
                        _cache.popitem(last=False)
                _cache[key] = embedding
                _cache.move_to_end(key)
            # Touch last_accessed_at (fire-and-forget, non-critical)
            try:
                await conn.execute(
                    "UPDATE embedding_cache SET last_accessed_at = datetime('now') "
                    "WHERE text_hash = ?",
                    (key,),
                )
                await conn.commit()
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
            return embedding
    except Exception:
        logger.warning("SQLite read failed for embedding cache", exc_info=True)

    return None


async def set(text: str, embedding: list[float], model: str = "") -> None:
    """Store embedding in L1 and persist to aiosqlite."""
    key = _hash(text, model)

    # L1 write
    async with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            _cache[key] = embedding
        else:
            if len(_cache) >= MAX_SIZE:
                _cache.popitem(last=False)
            _cache[key] = embedding

    # L2 — persist to aiosqlite (best-effort)
    try:
        conn = await _get_conn()
        emb_json = _serialize_embedding(embedding)
        await conn.execute(
            """INSERT OR REPLACE INTO embedding_cache (text_hash, model, embedding_json, created_at, last_accessed_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (key, model, emb_json),
        )
        await conn.commit()
    except Exception:
        logger.warning("SQLite write failed for embedding cache", exc_info=True)
        # L1 entry is kept — no data loss for the current session


async def clear() -> None:
    """Clear both L1 in-memory cache and aiosqlite table."""
    async with _lock:
        _cache.clear()
    try:
        conn = await _get_conn()
        await conn.execute("DELETE FROM embedding_cache")
        await conn.commit()
    except Exception:
        logger.warning("SQLite clear failed for embedding cache", exc_info=True)


def size() -> int:
    """Return the number of entries in the in-memory cache.

    Note: accesses ``_cache`` without holding ``_lock`` — the returned
    value may be slightly stale, which is acceptable for informational use.
    """
    return len(_cache)


# ── Async wrappers (thin — just delegate to the async base functions) ───────
# NOTE: Все операции с embedding_cache полностью асинхронны (aiosqlite).
# get() и set() используют aiosqlite для неблокирующего доступа к L2-кэшу.
# aget()/aset() — тонкие обёртки для обратной совместимости.
# Синхронных sqlite3-вызовов из async-путей нет.


async def aget(text: str, model: str = "") -> list[float] | None:
    """Async wrapper around ``get`` — kept for backward compatibility."""
    return await get(text, model)


async def aset(text: str, embedding: list[float], model: str = "") -> None:
    """Async wrapper around ``set`` — kept for backward compatibility."""
    await set(text, embedding, model)


# ── Resource cleanup ────────────────────────────────────────────────────────


async def close() -> None:
    """Close the shared aiosqlite connection (call during application shutdown).

    Safe to call multiple times — idempotent.
    """
    global _async_conn
    async with _conn_lock:
        if _async_conn is not None:
            try:
                await _async_conn.close()
            except Exception:
                logger.warning("Failed to close embedding cache DB", exc_info=True)
            finally:
                _async_conn = None


async def health_check() -> dict[str, Any]:
    """Return health status of the embedding cache.

    Returns:
        Dict with ``ok`` (bool), ``l1_size`` (int), ``l2_connected`` (bool),
        and ``error`` (str|None).
    """
    try:
        conn = await _get_conn()
        async with conn.execute("SELECT COUNT(*) FROM embedding_cache") as cursor:
            row = await cursor.fetchone()
            l2_count = row[0] if row else 0
        return {
            "ok": True,
            "l1_size": size(),
            "l2_count": l2_count,
            "l2_connected": True,
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "l1_size": size(),
            "l2_count": 0,
            "l2_connected": False,
            "error": str(exc),
        }
