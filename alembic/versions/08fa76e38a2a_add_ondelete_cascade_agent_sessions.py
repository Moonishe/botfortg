"""add_ondelete_cascade_agent_sessions

Revision ID: 08fa76e38a2a
Revises: c1d2e3f4a5b6
Create Date: 2026-06-12 19:48:09.808892

Add ondelete="CASCADE" to two ForeignKeys so that deleting a user
cascades to their agent_sessions, and deleting a session cascades to
its messages.

SQLite doesn't support ALTER CONSTRAINT and the existing FKs have no
user-visible name — so we use explicit table rebuild via op.execute()
(raw SQL).  This is equivalent to what Alembic's batch_alter_table
does internally, but with explicit control over the FK definition.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "08fa76e38a2a"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── helpers ────────────────────────────────────────────────────────

_AGENT_SESSION_MESSAGES_DDL_TEMPLATE = """
CREATE TABLE {tmp} (
    id          INTEGER  NOT NULL  PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER  NOT NULL,
    role        VARCHAR(16) NOT NULL,
    content     TEXT     NOT NULL,
    created_at  DATETIME NOT NULL,
    FOREIGN KEY(session_id) REFERENCES agent_sessions(id){on_delete}
)
"""

_AGENT_SESSIONS_DDL_TEMPLATE = """
CREATE TABLE {tmp} (
    id           INTEGER  NOT NULL  PRIMARY KEY AUTOINCREMENT,
    user_id      BIGINT   NOT NULL,
    session_type VARCHAR(32) NOT NULL,
    started_at   DATETIME NOT NULL,
    ended_at     DATETIME,
    summary      TEXT,
    turn_count   INTEGER  NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id){on_delete}
)
"""


def _rebuild_table(
    table_name: str,
    column_list: str,
    ddl_create: str,
) -> None:
    """Drop & recreate *table_name* via a temporary table.

    Steps (SQLite-safe):
        1.  CREATE TABLE ``_tmp_X`` with the new schema.
        2.  INSERT INTO ``_tmp_X``  SELECT * FROM ``X``.
        3.  DROP TABLE ``X``.
        4.  ALTER TABLE ``_tmp_X`` RENAME TO ``X``.

    ``column_list`` is a comma-separated list of column names (for
    the INSERT … SELECT) — since we always match old/new columns 1:1
    this is just ``*``.
    """
    # Disable FK checks for the duration of the table swap so that
    # dropping the old table while a dependent FK still references it
    # doesn't raise "foreign key mismatch".
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        op.execute(ddl_create)
        op.execute(
            f"INSERT INTO _tmp_{table_name} SELECT {column_list} FROM {table_name}"
        )
        op.execute(f"DROP TABLE {table_name}")
        op.execute(f"ALTER TABLE _tmp_{table_name} RENAME TO {table_name}")
    finally:
        op.execute("PRAGMA foreign_keys=ON")


# ── upgrade / downgrade ────────────────────────────────────────────


def upgrade() -> None:
    """Добавить ON DELETE CASCADE к двум ForeignKey."""

    # 1.  agent_session_messages.session_id → agent_sessions.id
    _rebuild_table(
        table_name="agent_session_messages",
        column_list="*",
        ddl_create=_AGENT_SESSION_MESSAGES_DDL_TEMPLATE.format(
            tmp="_tmp_agent_session_messages", on_delete=" ON DELETE CASCADE"
        ),
    )
    # Recreate the index that was lost in the rebuild
    op.create_index(
        "ix_agent_session_messages_session_id",
        "agent_session_messages",
        ["session_id"],
    )

    # 2.  agent_sessions.user_id → users.id
    _rebuild_table(
        table_name="agent_sessions",
        column_list="*",
        ddl_create=_AGENT_SESSIONS_DDL_TEMPLATE.format(
            tmp="_tmp_agent_sessions", on_delete=" ON DELETE CASCADE"
        ),
    )
    op.create_index(
        "ix_agent_sessions_user_id",
        "agent_sessions",
        ["user_id"],
    )


def downgrade() -> None:
    """Убрать ON DELETE CASCADE — вернуть поведение NO ACTION."""

    # 1.  agent_sessions.user_id → users.id  (revert)
    _rebuild_table(
        table_name="agent_sessions",
        column_list="*",
        ddl_create=_AGENT_SESSIONS_DDL_TEMPLATE.format(
            tmp="_tmp_agent_sessions", on_delete=""
        ),
    )
    op.create_index(
        "ix_agent_sessions_user_id",
        "agent_sessions",
        ["user_id"],
    )

    # 2.  agent_session_messages.session_id → agent_sessions.id  (revert)
    _rebuild_table(
        table_name="agent_session_messages",
        column_list="*",
        ddl_create=_AGENT_SESSION_MESSAGES_DDL_TEMPLATE.format(
            tmp="_tmp_agent_session_messages", on_delete=""
        ),
    )
    op.create_index(
        "ix_agent_session_messages_session_id",
        "agent_session_messages",
        ["session_id"],
    )
