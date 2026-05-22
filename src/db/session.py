import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.db.models import Base

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, future=True)
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
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA wal_autocheckpoint=1000")
    cursor.close()


# Alembic is configured for schema migrations (alembic/).
# Future model changes should be captured via:
#   alembic revision --autogenerate -m "description"
# The ALTER TABLE blocks below handle legacy migrations for existing DBs.

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
        if (
            "duplicate column name" in str(e).lower()
            or "already exists" in str(e).lower()
        ):
            logger.debug(
                "Migration for related_memory_id → memory_links: already applied"
            )
        else:
            raise


async def init_db() -> None:
    settings.data_dir  # триггерит создание директории
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.execute(text("PRAGMA cache_size=-64000"))  # 64 MB page cache
        await conn.execute(text("PRAGMA mmap_size=134217728"))  # 128 MB mmap
        await conn.execute(text("PRAGMA busy_timeout=30000"))  # 30s busy timeout
        await conn.execute(text("PRAGMA temp_store=MEMORY"))  # temp tables in memory
        await conn.execute(
            text("PRAGMA wal_autocheckpoint=1000")
        )  # checkpoint every 1000 pages

        # Schema bootstrap: ORM models → create_all.
        # Alembic (alembic upgrade head) is the canonical migration path and
        # should be run as a pre-start step before the event loop starts.
        # See "Migration Workflow" in alembic/README or run() in main.py.
        await conn.run_sync(Base.metadata.create_all)

        # FTS5 virtual tables are not tracked by Alembic — raw SQL.
        for stmt in _FTS_SETUP:
            await conn.execute(text(stmt))
        for stmt in _MEMORY_FTS_SETUP:
            await conn.execute(text(stmt))

        # Data migration (not schema): related_memory_id → memory_links.
        await _migrate_related_memory_to_links(conn)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
