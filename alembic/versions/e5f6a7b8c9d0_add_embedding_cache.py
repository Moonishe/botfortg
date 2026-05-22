"""add_embedding_cache — persistent SQLite-backed embedding cache.

Revision ID: e5f6a7b8c9d0
Revises: d1c2e3f4a5b6, b8e4f2a3c5d1
Create Date: 2026-05-22 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = (
    "d1c2e3f4a5b6",
    "b8e4f2a3c5d1",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — create embedding_cache table."""
    # Guard: the table may already exist if created by the initial migration
    # (0ea3133e3615 now uses Base.metadata.create_all).
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "embedding_cache" in inspector.get_table_names():
        return
    op.create_table(
        "embedding_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False, server_default=""),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_embedding_cache_text_hash",
        "embedding_cache",
        ["text_hash"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema — drop embedding_cache table."""
    op.drop_index("ix_embedding_cache_text_hash", table_name="embedding_cache")
    op.drop_table("embedding_cache")
