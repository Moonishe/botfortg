"""add_hybrid_approval_fields_to_pending_actions

Revision ID: 7810dc27b574
Revises: c0r0n0j0b0s
Create Date: 2026-06-16 12:10:42.825595

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "7810dc27b574"
down_revision: str | Sequence[str] | None = "c0r0n0j0b0s"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    """Проверить существование таблицы через sa.inspect (SQLite)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    """Проверить существование колонки через PRAGMA table_info (SQLite)."""
    if not _table_exists(table_name):
        return False
    conn = op.get_bind()
    result = conn.execute(sa.text(f"PRAGMA table_info({table_name})"))
    return any(row[1] == column_name for row in result)


def _index_exists(index_name: str, table_name: str) -> bool:
    """Проверить наличие индекса через PRAGMA index_list (SQLite)."""
    if not _table_exists(table_name):
        return False
    conn = op.get_bind()
    result = conn.execute(sa.text(f"PRAGMA index_list({table_name})"))
    return any(row[1] == index_name for row in result)


def upgrade() -> None:
    """Add hybrid approval routing columns to pending_actions.

    This migration is idempotent: it safely skips columns/indexes that already
    exist (e.g. on a fresh DB created from the updated initial schema).
    """
    # Add columns that may already exist on a fresh DB but are missing from an
    # older DB.  Route/verb/risk are non-nullable, so supply server defaults for
    # backfill and drop them afterwards.
    with op.batch_alter_table("pending_actions", schema=None) as batch_op:
        if not _column_exists("pending_actions", "expires_at"):
            batch_op.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))
        if not _column_exists("pending_actions", "hmac_signature"):
            batch_op.add_column(
                sa.Column("hmac_signature", sa.String(length=64), nullable=True)
            )
        if not _column_exists("pending_actions", "route"):
            batch_op.add_column(
                sa.Column(
                    "route",
                    sa.String(length=8),
                    nullable=False,
                    server_default="db",
                )
            )
        if not _column_exists("pending_actions", "verb"):
            batch_op.add_column(
                sa.Column(
                    "verb",
                    sa.String(length=16),
                    nullable=False,
                    server_default="send",
                )
            )
        if not _column_exists("pending_actions", "risk"):
            batch_op.add_column(
                sa.Column(
                    "risk",
                    sa.String(length=16),
                    nullable=False,
                    server_default="low",
                )
            )
        if not _column_exists("pending_actions", "human_summary"):
            batch_op.add_column(sa.Column("human_summary", sa.Text(), nullable=True))

    # Create indexes idempotently.
    if not _index_exists("ix_pending_actions_expires_at", "pending_actions"):
        op.create_index(
            op.f("ix_pending_actions_expires_at"),
            "pending_actions",
            ["expires_at"],
            unique=False,
        )
    if not _index_exists("ix_pending_actions_kind", "pending_actions"):
        op.create_index(
            op.f("ix_pending_actions_kind"),
            "pending_actions",
            ["kind"],
            unique=False,
        )

    # After backfilling existing rows, drop the server defaults so the model
    # defaults (set in Python) are the source of truth.
    with op.batch_alter_table("pending_actions", schema=None) as batch_op:
        if _column_exists("pending_actions", "route"):
            batch_op.alter_column("route", server_default=None)
        if _column_exists("pending_actions", "verb"):
            batch_op.alter_column("verb", server_default=None)
        if _column_exists("pending_actions", "risk"):
            batch_op.alter_column("risk", server_default=None)


def downgrade() -> None:
    """Remove hybrid approval routing columns from pending_actions."""
    with op.batch_alter_table("pending_actions", schema=None) as batch_op:
        if _index_exists("ix_pending_actions_kind", "pending_actions"):
            batch_op.drop_index(op.f("ix_pending_actions_kind"))
        if _index_exists("ix_pending_actions_expires_at", "pending_actions"):
            batch_op.drop_index(op.f("ix_pending_actions_expires_at"))
        if _column_exists("pending_actions", "human_summary"):
            batch_op.drop_column("human_summary")
        if _column_exists("pending_actions", "risk"):
            batch_op.drop_column("risk")
        if _column_exists("pending_actions", "verb"):
            batch_op.drop_column("verb")
        if _column_exists("pending_actions", "route"):
            batch_op.drop_column("route")
        if _column_exists("pending_actions", "hmac_signature"):
            batch_op.drop_column("hmac_signature")
        if _column_exists("pending_actions", "expires_at"):
            batch_op.drop_column("expires_at")
