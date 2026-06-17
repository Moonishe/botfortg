"""add_index_memory_tier_and_constraints

Revision ID: b1c2d3e4f5a6
Revises: a95db707b7e9
Create Date: 2026-06-11 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a95db707b7e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(index_name: str, table_name: str) -> bool:
    """Check if an index exists on the given table (SQLite)."""
    conn = op.get_bind()
    result = conn.execute(sa.text(f"PRAGMA index_list({table_name})"))
    return any(row[1] == index_name for row in result)


def upgrade() -> None:
    """Add composite index on memories, unique constraint on memory_links, composite index on contacts."""

    # These indexes may already exist if the initial migration's
    # Base.metadata.create_all() created them from ORM model definitions.
    # Guard each one to be idempotent.

    # 1. Composite index for deep_memory queries: WHERE memory_tier=2 AND is_active AND user_id=?
    if not _index_exists("ix_mem_user_active_tier", "memories"):
        op.create_index(
            "ix_mem_user_active_tier",
            "memories",
            ["user_id", "is_active", "memory_tier"],
            unique=False,
        )

    # 2. Unique index on memory_links to prevent duplicate (user_id, source_id, target_id)
    # ponytail: SQLite cannot add constraints via ALTER; unique index is equivalent.
    if not _index_exists("uq_memory_link", "memory_links"):
        op.create_index(
            "uq_memory_link",
            "memory_links",
            ["user_id", "source_id", "target_id"],
            unique=True,
        )

    # 3. Composite index for 90% of contact queries filtering by (user_id, peer_id)
    if not _index_exists("ix_contact_user_peer", "contacts"):
        op.create_index(
            "ix_contact_user_peer",
            "contacts",
            ["user_id", "peer_id"],
            unique=False,
        )


def downgrade() -> None:
    """Remove added indexes and unique constraint."""

    op.drop_index("ix_contact_user_peer", table_name="contacts")

    op.drop_index("uq_memory_link", table_name="memory_links")

    op.drop_index("ix_mem_user_active_tier", table_name="memories")
