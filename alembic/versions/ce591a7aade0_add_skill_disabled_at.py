"""add skill disabled_at

Revision ID: ce591a7aade0
Revises: af87719466fc
Create Date: 2026-06-18 00:38:04.476640

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ce591a7aade0"
down_revision: str | Sequence[str] | None = "af87719466fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add disabled_at timestamp to skills if it does not exist.

    The initial schema migration creates all tables from the current ORM
    definitions, so the column may already exist on a fresh database. We
    therefore guard the ADD COLUMN with an inspector check.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {col["name"] for col in inspector.get_columns("skills")}
    if "disabled_at" not in columns:
        op.add_column(
            "skills",
            sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    """Drop disabled_at timestamp from skills if it exists."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {col["name"] for col in inspector.get_columns("skills")}
    if "disabled_at" in columns:
        op.drop_column("skills", "disabled_at")
