"""Persistent, thread-safe lazy-init SQLite connection.

One long-lived connection per logical DB. WAL journal, busy timeout,
NORMAL synchronous mode. DDL and optional init callback are executed
once under a reentrant lock. Callers use ``with db.locked() as conn:``
to serialise operations and trigger lazy initialisation.
"""

from __future__ import annotations

import contextlib
import logging
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from src.config import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

# Unicode letters are valid in SQLite identifiers; we allow them.
# The regex guards against delimiter/operator injection when identifiers
# MUST be interpolated (SQLite does not support parameterized identifiers).
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_\u0080-\uffff][A-Za-z0-9_\u0080-\uffff]*$")


def get_db_path() -> Path:
    """Resolve the SQLite database file path from settings.database_url."""
    db_url = str(settings.database_url)
    parsed = urlparse(db_url)
    if parsed.scheme == "sqlite":
        db_path = Path(parsed.path.lstrip("/"))
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        return db_path
    return settings.data_dir / "app.db"


class PersistentSQLite:
    """Thread-safe lazy-init persistent SQLite connection.

    Usage::

        _db = PersistentSQLite(
            db_path=str(settings.data_dir / "app.db"),
            table_ddl="CREATE TABLE IF NOT EXISTS t(...)",
            extra_sql=["CREATE INDEX IF NOT EXISTS ..."],
            init_fn=_migrate_legacy,
        )

        def _do_sql() -> None:
            with _db.locked() as conn:
                conn.execute("INSERT ...")
                conn.commit()
    """

    def __init__(
        self,
        db_path: str | Path,
        table_ddl: str,
        *,
        init_fn: Callable[[sqlite3.Connection], None] | None = None,
        extra_sql: list[str] | None = None,
        busy_timeout_ms: int = 30_000,
    ) -> None:
        self._path = str(db_path)
        self._table_ddl = table_ddl
        self._init_fn = init_fn
        self._extra_sql = extra_sql or []
        self._busy_timeout_ms = busy_timeout_ms
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._ready = False

    @contextlib.contextmanager
    def locked(self) -> Iterator[sqlite3.Connection]:
        """Acquire the lock, lazily bootstrap, and yield the connection.

        The caller holds the lock for the entire ``with`` block, serialising
        all operations on this connection. The lock is reentrant so nested
        calls are safe.  *However*, the init_fn passed to ``__init__`` must
        NOT call ``locked()`` on the same instance — ``_ready`` is still
        ``False`` during init, so the inner call re-enters ``_bootstrap()``
        and creates a leaked second connection.
        """
        with self._lock:
            if not self._ready:
                self._conn = self._bootstrap()
                self._ready = True
            yield cast(sqlite3.Connection, self._conn)

    def _bootstrap(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_ms)}")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(self._table_ddl)
            for stmt in self._extra_sql:
                conn.execute(stmt)
            conn.commit()
            if self._init_fn is not None:
                self._init_fn(conn)
            return conn
        except Exception:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
            logger.exception("PersistentSQLite init failed (%s)", self._path)
            raise

    def close(self) -> None:
        """Checkpoint WAL and close the connection.

        Idempotent. After close, the next ``locked()`` call will re-bootstrap.
        """
        with self._lock:
            conn = self._conn
            self._conn = None
            self._ready = False
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                with contextlib.suppress(Exception):
                    conn.close()
                logger.debug("PersistentSQLite closed (%s)", self._path)


def migrate_from_app_db(
    conn: sqlite3.Connection,
    table_name: str,
    columns: list[str],
    *,
    old_db_path: Path,
    log_label: str,
) -> None:
    """Copy legacy rows from the old app.db into *conn* if *table_name* is empty.

    Idempotent: skips when the target table is non-empty, the old DB is missing,
    or any error occurs. Intended as an ``init_fn`` for :class:`PersistentSQLite`.

    Writes are wrapped in an explicit transaction so that a mid-migration failure
    leaves the target table empty rather than partially populated.

    *Note:* ``table_name`` and ``columns`` are interpolated into SQL because SQLite
    does not support parameterized identifiers. Callers must pass trusted literals.
    """
    # Defense-in-depth: reject identifiers that don't match SQLite's rules.
    if not _SQL_IDENTIFIER_RE.match(table_name):
        raise ValueError(f"Invalid SQL table name: {table_name!r}")
    for col in columns:
        if not _SQL_IDENTIFIER_RE.match(col):
            raise ValueError(f"Invalid SQL column name: {col!r}")

    try:
        # table_name is a trusted literal passed by callers; SQLite does not
        # support parameterized identifiers.
        row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()  # noqa: S608
        if row and row[0] > 0:
            return
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return
        logger.warning(
            "%s migration: unable to check %s table: %s", log_label, table_name, exc
        )
        return

    if not old_db_path.exists():
        return

    try:
        col_csv = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        with sqlite3.connect(str(old_db_path)) as old_conn:
            old_conn.execute("PRAGMA busy_timeout=30000")
            # Identifiers are trusted literals; see function docstring.
            old_rows = old_conn.execute(
                f"SELECT {col_csv} FROM {table_name}"  # noqa: S608
            ).fetchall()
        if old_rows:
            # Wrap writes in an explicit transaction so a mid-migration
            # failure rolls back completely, leaving the target table empty.
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Identifiers are trusted literals; see function docstring.
                conn.executemany(
                    f"INSERT INTO {table_name}({col_csv}) VALUES ({placeholders})",  # noqa: S608
                    old_rows,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            else:
                logger.info(
                    "Migrated %d %s entries from %s",
                    len(old_rows),
                    log_label,
                    old_db_path,
                )
    except Exception:
        logger.debug("%s migration not possible", log_label, exc_info=True)
