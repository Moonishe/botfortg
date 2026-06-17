"""add_timers_table

Revision ID: af87719466fc
Revises: 01d9cbf0c45e
Create Date: 2026-06-17 11:42:47.436882

"""

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "af87719466fc"
down_revision: str | Sequence[str] | None = "01d9cbf0c45e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the timers table if it does not exist.

    The legacy raw sqlite3 implementation already created a table with the
    same name and compatible columns. This migration ensures the table exists
    for deployments where the ORM path is used first.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS timers (
            timer_id INTEGER PRIMARY KEY,
            fire_at TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT ''
        )
        """
    )


def downgrade() -> None:
    """Drop the timers table."""
    op.execute("DROP TABLE IF EXISTS timers")
