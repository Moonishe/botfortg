"""add index skills disabled_at

Revision ID: 4003d4e0335c
Revises: ce591a7aade0
Create Date: 2026-06-18 01:13:13.367820

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4003d4e0335c"
down_revision: str | Sequence[str] | None = "ce591a7aade0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add index on skills.disabled_at if it does not exist.

    The initial schema migration creates tables and indexes from the
    current ORM definitions, so the index may already exist on a fresh DB.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = {idx["name"] for idx in inspector.get_indexes("skills")}
    if "ix_skills_disabled_at" not in indexes:
        op.create_index("ix_skills_disabled_at", "skills", ["disabled_at"])


def downgrade() -> None:
    """Drop index on skills.disabled_at if it exists."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = {idx["name"] for idx in inspector.get_indexes("skills")}
    if "ix_skills_disabled_at" in indexes:
        op.drop_index("ix_skills_disabled_at", table_name="skills")
