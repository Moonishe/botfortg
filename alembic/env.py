import asyncio
import importlib
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- Load models directly to avoid circular imports ---
# The models package uses string-based forward references so there are no
# cross-module import cycles.  Importing via importlib still bypasses the
# src/db/__init__.py entry point and the circular import chain.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
_models = importlib.import_module("db.models")
Base = _models.Base

target_metadata = Base.metadata


# FTS5 virtual tables are created by init_db() via raw SQL, not by ORM.
# Exclude them from Alembic autogenerate so it doesn't try to drop/recreate them.
_FTS5_TABLE_NAMES = frozenset(
    {
        "messages_fts",
        "messages_fts_data",
        "messages_fts_docsize",
        "messages_fts_config",
        "messages_fts_idx",
        "agent_session_messages_fts",
        "agent_session_messages_fts_data",
        "agent_session_messages_fts_docsize",
        "agent_session_messages_fts_config",
        "agent_session_messages_fts_idx",
        "memories_fts",
        "memories_fts_data",
        "memories_fts_docsize",
        "memories_fts_config",
        "memories_fts_idx",
    }
)


def include_object(obj, name, type_, reflected, compare_to):
    """Skip FTS5 internal tables — they are managed by init_db() in session.py."""
    if type_ == "table" and name in _FTS5_TABLE_NAMES:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well. By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Helper: configure context and run migrations on a sync connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using the project's async engine URL.

    Reads the real database URL from Settings (respects .env overrides)
    and creates an async engine for Alembic to use.
    """
    from src.config import settings

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.database_url

    # Add SQLite PRAGMAs for reliability (WAL mode + busy_timeout)
    connect_args = {}
    if "sqlite" in settings.database_url:
        connect_args = {
            "timeout": 30,  # 30 second busy_timeout
            "check_same_thread": False,  # Required for async usage
        }

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    # Set PRAGMAs via event listener (WAL mode + synchronous=NORMAL)
    from sqlalchemy import event

    @event.listens_for(connectable.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        if "sqlite" in settings.database_url:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (async)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
