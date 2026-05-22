"""add_smart_cache

Revision ID: a7c3d9e1f0b2
Revises: 9a4b2c7d8e3f
Create Date: 2026-05-22 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c3d9e1f0b2"
down_revision: Union[str, Sequence[str], None] = "9a4b2c7d8e3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — create smart_cache table."""
    # Guard: the table may already exist if created by the initial migration
    # (0ea3133e3615 now uses Base.metadata.create_all).
    if op.has_table("smart_cache"):
        return
    op.create_table(
        "smart_cache",
        sa.Column("cache_key", sa.String(512), nullable=False),
        sa.Column("cache_value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="unknown"),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("importance_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("graduated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    op.create_index(
        "ix_smart_cache_owner_id", "smart_cache", ["owner_id"], unique=False
    )
    op.create_index(
        "ix_smart_cache_accessed_at", "smart_cache", ["accessed_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema — drop smart_cache table."""
    op.drop_index("ix_smart_cache_accessed_at", table_name="smart_cache")
    op.drop_index("ix_smart_cache_owner_id", table_name="smart_cache")
    op.drop_table("smart_cache")
