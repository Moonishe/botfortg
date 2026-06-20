import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import Any, NamedTuple

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError, InvalidRequestError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import PROJECT_ROOT, settings
from src.db.models import Base

logger = logging.getLogger(__name__)

# ── Nested transaction tracking ──────────────────────────────────────
# Context variable to detect when get_session() is called inside another
# get_session() block. Enables automatic savepoint (begin_nested) usage.
#
# Stores (asyncio_task_id, AsyncSession) to prevent concurrent session
# sharing across tasks. When a child task inherits the ContextVar from
# its parent (because create_task copies the context), the task IDs will
# mismatch and the child gets its own session instead of a dangerously
# shared savepoint on the parent's session.


class _OuterSession(NamedTuple):
    task_id: int
    session: AsyncSession


_outer_session: ContextVar[_OuterSession | None] = ContextVar(
    "_outer_session", default=None
)

# SQLite in-memory databases evaporate when their connection is released, so
# keep exactly one connection alive for the entire process lifetime.
_engine_kwargs: dict[str, Any] = {
    "future": True,
    "connect_args": {"check_same_thread": False},
}
if str(settings.database_url).endswith(":memory:"):
    from sqlalchemy.pool import StaticPool

    _engine_kwargs["poolclass"] = StaticPool

engine = create_async_engine(
    settings.database_url,
    **_engine_kwargs,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """Ensure performance PRAGMAs are set on every new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-64000")
    cursor.execute("PRAGMA mmap_size=134217728")  # 128 MB (safe for containers)
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA wal_autocheckpoint=1000")
    cursor.close()


# Alembic is the CANONICAL schema migration path.
# Future model changes MUST be captured via:
#   alembic revision --autogenerate -m "description"
#   alembic upgrade head
# Base.metadata.create_all is only used as a one-time bootstrap fallback
# when the alembic_version table is missing (fresh DB / direct run of main()).

# SQLite FTS5: virtual table + триггеры синхронизации с messages.
# Хранит rowid = messages.id.
_FTS_SETUP = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        text,
        transcript,
        extracted_text,
        sender_name,
        content='messages',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, text, transcript, extracted_text, sender_name)
        VALUES (new.id, new.text, new.transcript, new.extracted_text, new.sender_name);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, text, transcript, extracted_text, sender_name)
        VALUES('delete', old.id, old.text, old.transcript, old.extracted_text, old.sender_name);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, text, transcript, extracted_text, sender_name)
        VALUES('delete', old.id, old.text, old.transcript, old.extracted_text, old.sender_name);
        INSERT INTO messages_fts(rowid, text, transcript, extracted_text, sender_name)
        VALUES (new.id, new.text, new.transcript, new.extracted_text, new.sender_name);
    END;
    """,
]

# Agent Session FTS5: external-content virtual table linked to agent_session_messages.
# Triggers required to keep the FTS index synchronised with the content table.
_SESSION_FTS_SETUP = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS agent_session_messages_fts USING fts5(
        content, role, session_id UNINDEXED,
        content='agent_session_messages', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    """,
    """
    CREATE TRIGGER IF NOT EXISTS agent_session_messages_fts_ai AFTER INSERT ON agent_session_messages BEGIN
        INSERT INTO agent_session_messages_fts(rowid, content, role)
        VALUES (new.id, new.content, new.role);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS agent_session_messages_fts_ad AFTER DELETE ON agent_session_messages BEGIN
        INSERT INTO agent_session_messages_fts(agent_session_messages_fts, rowid, content, role)
        VALUES('delete', old.id, old.content, old.role);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS agent_session_messages_fts_au AFTER UPDATE ON agent_session_messages BEGIN
        INSERT INTO agent_session_messages_fts(agent_session_messages_fts, rowid, content, role)
        VALUES('delete', old.id, old.content, old.role);
        INSERT INTO agent_session_messages_fts(rowid, content, role)
        VALUES (new.id, new.content, new.role);
    END;
    """,
]

# Memory FTS5: virtual table + триггеры синхронизации с memories.
_MEMORY_FTS_SETUP = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        fact, sentiment, cluster_topic,
        content='memories',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, fact, sentiment, cluster_topic)
        VALUES (new.id, new.fact, new.sentiment, new.cluster_topic);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, fact, sentiment, cluster_topic)
        VALUES('delete', old.id, old.fact, old.sentiment, old.cluster_topic);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, fact, sentiment, cluster_topic)
        VALUES('delete', old.id, old.fact, old.sentiment, old.cluster_topic);
        INSERT INTO memories_fts(rowid, fact, sentiment, cluster_topic)
        VALUES (new.id, new.fact, new.sentiment, new.cluster_topic);
    END;
    """,
]


async def _migrate_related_memory_to_links(conn) -> None:
    """One-time data migration: related_memory_id → memory_links.

    Copies old-style ``memories.related_memory_id`` + ``memories.relation_type``
    into the ``memory_links`` table if the link doesn't already exist.
    Safe to re-run: checks for existing links before inserting.

    This is a DATA migration, not a schema migration, so it lives here
    rather than in Alembic.
    """
    try:
        result = await conn.execute(
            text(
                "SELECT id, related_memory_id, relation_type FROM memories WHERE related_memory_id IS NOT NULL"
            )
        )
        for row in result.all():
            mid, related_id, rel_type = row
            # Проверить нет ли уже связи в memory_links
            check = await conn.execute(
                text(
                    "SELECT id FROM memory_links WHERE source_id = :sid AND target_id = :tid"
                ),
                {"sid": mid, "tid": related_id},
            )
            if not check.first():
                await conn.execute(
                    text(
                        "INSERT INTO memory_links (user_id, source_id, target_id, weight, relation_type, created_at) "
                        "SELECT user_id, :sid, :tid, 0.7, :rel, datetime('now') FROM memories WHERE id = :sid"
                    ),
                    {"sid": mid, "tid": related_id, "rel": rel_type},
                )
    except Exception as e:
        # This is a *data* migration that has already been moved to alembic
        # for new deployments, so any error here means either (a) the data
        # was already migrated previously, or (b) the legacy columns are
        # gone (e.g. they were dropped by a later schema migration).  Both
        # cases are safe to ignore — we never want this routine to crash
        # ``init_db()`` and prevent the application from starting.
        if isinstance(e, (OperationalError, IntegrityError)):
            logger.debug(
                "Migration for related_memory_id → memory_links: skipped (%s)",
                e.__class__.__name__,
            )
        else:
            raise


async def init_db() -> None:
    """Initialise database: PRAGMAs, schema, FTS5 tables, data migrations.

    Schema management policy
    ------------------------
    Alembic is the **canonical** migration path.  ``run()`` in ``main.py``
    runs ``alembic upgrade head`` synchronously before the event loop starts,
    so by the time this function executes the ``alembic_version`` table exists
    and all migrations have been applied.

    ``Base.metadata.create_all`` is only used as a **one-time bootstrap
    fallback** when the ``alembic_version`` table is missing (fresh database,
    or ``main()`` called directly without the ``run()`` wrapper).  In that
    case the head revision is stamped immediately so subsequent runs can use
    Alembic.

    This avoids the "belt and suspenders" anti-pattern where both Alembic
    *and* ``create_all`` run on every startup, which can silently hide
    missing migrations (developer adds a column to an ORM model but forgets
    ``alembic revision --autogenerate`` → ``create_all`` silently creates it,
    masking the desync).
    """
    settings.data_dir  # триггерит создание директории
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.execute(text("PRAGMA cache_size=-64000"))  # 64 MB page cache
        await conn.execute(text("PRAGMA mmap_size=134217728"))  # 128 MB mmap
        await conn.execute(text("PRAGMA busy_timeout=30000"))  # 30s busy timeout
        await conn.execute(text("PRAGMA foreign_keys=ON"))  # enforce FK constraints
        # ВАЖНО: После включения foreign_keys=ON существующие orphan-строки
        # (например, messages.source_id -> несуществующий MonitoredSource)
        # вызовут IntegrityError. Для диагностики: PRAGMA foreign_key_check;
        # Рекомендуется запускать при старте и чистить orphans через data migration.
        await conn.execute(text("PRAGMA temp_store=MEMORY"))  # temp tables in memory
        await conn.execute(
            text("PRAGMA wal_autocheckpoint=1000")
        )  # checkpoint every 1000 pages

        # --- Schema: Alembic-canonical, create_all as bootstrap fallback ---
        # Check if alembic_version table exists — the canonical marker
        # that the ORM schema has been applied. This is immune to FTS5
        # virtual tables and other non-ORM artefacts in sqlite_master.
        _has_orm_tables = await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name = 'alembic_version'"
            )
        )
        _has_orm_tables = _has_orm_tables.first() is not None

        if _has_orm_tables:
            logger.debug(
                "ORM tables found — schema is up-to-date; "
                "skipping Base.metadata.create_all"
            )
        else:
            logger.warning(
                "No ORM tables found — bootstrapping schema via "
                "Base.metadata.create_all. This happens on fresh DB, "
                "or after Railway volume reset."
            )
            await conn.run_sync(Base.metadata.create_all)

        # Stamp alembic head if version table was just created by create_all
        # (or is missing for any other reason — e.g. volume reset).
        # Reuses _has_orm_tables from the check above — avoids a redundant
        # sqlite_master query.
        if not _has_orm_tables:
            # Stamp the head revision so Alembic knows all migrations are
            # already applied (create_all built the current ORM schema).
            try:
                _alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
                _script = ScriptDirectory.from_config(_alembic_cfg)
                head_rev = _script.get_current_head()
                await conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS alembic_version "
                        "(version_num VARCHAR(32) NOT NULL)"
                    )
                )
                await conn.execute(
                    text(
                        "INSERT OR IGNORE INTO alembic_version (version_num) VALUES (:rev)"
                    ),
                    {"rev": head_rev},
                )
                logger.info(
                    "Stamped alembic head revision %s after create_all bootstrap",
                    head_rev,
                )
            except Exception:
                logger.error(
                    "Failed to stamp alembic head revision — "
                    "is alembic.ini present at %s? "
                    "Schema was created via create_all; "
                    "Alembic will stamp on next normal startup via run().",
                    PROJECT_ROOT / "alembic.ini",
                    exc_info=True,
                )
                # Don't re-raise — schema was created, Alembic stamps next time

        # FTS5 virtual tables are not tracked by Alembic — raw SQL.
        for stmt in _FTS_SETUP:
            await conn.execute(text(stmt))
        for stmt in _SESSION_FTS_SETUP:
            await conn.execute(text(stmt))
        for stmt in _MEMORY_FTS_SETUP:
            await conn.execute(text(stmt))

        # Data migration (not schema): related_memory_id → memory_links.
        await _migrate_related_memory_to_links(conn)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async database session with automatic commit/rollback.

    **Nested-call safety:** when ``get_session()`` is called while already
    inside another ``get_session()`` block **in the same asyncio task**,
    the inner call uses an SQLAlchemy SAVEPOINT (``begin_nested()``)
    instead of creating a separate connection.

    **Cross-task safety:** if a child task (created via
    ``asyncio.create_task()``) inherits the outer session from the parent's
    ContextVar, the task IDs will mismatch and the child receives its own
    independent session — avoiding concurrent use of the same AsyncSession.

    Example::

        async with get_session() as outer:
            await outer.execute(...)          # outer transaction
            async with get_session() as inner:
                await inner.execute(...)      # SAVEPOINT — isolated
                # rollback here only affects the savepoint
            # outer transaction continues safely
    """
    outer_data = _outer_session.get()
    current_task_id = id(asyncio.current_task())

    if outer_data is not None and outer_data.task_id == current_task_id:
        # Nested call in the SAME task — use savepoint on the outer session.
        # Distinguish "outer session is dead/unusable" (InvalidRequestError /
        # OperationalError from a previously-rolled-back connection) from
        # genuine business errors raised by the yielded body: the former
        # warrants falling through to a fresh session, the latter MUST
        # propagate to the caller. A bare ``except Exception`` here would
        # silently swallow every exception raised inside a nested
        # ``get_session()`` block and corrupt data with a clean-looking
        # success.
        try:
            async with outer_data.session.begin_nested():
                yield outer_data.session
            return
        except (InvalidRequestError, OperationalError) as e:
            logger.debug(
                "Outer session %r is not usable (%s) — falling back to new session",
                outer_data.session,
                e.__class__.__name__,
            )
            # Clear the stale ContextVar entry so subsequent nested calls
            # won't hit the same dead session.
            _outer_session.set(None)

    # Either no outer session, or a cross-task inheritance — create new session
    async with SessionLocal() as session:
        token: Token[  # type: ignore[valid-type]
            _OuterSession | None
        ] = _outer_session.set(_OuterSession(current_task_id, session))
        try:
            yield session
            await session.commit()
        except Exception:
            try:
                await session.rollback()
            except Exception:
                logger.debug("Rollback failed in get_session cleanup", exc_info=True)
            raise
        finally:
            _outer_session.reset(token)
