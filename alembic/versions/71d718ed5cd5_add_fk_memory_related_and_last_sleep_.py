"""add_fk_memory_related_and_last_sleep_notification_date

Revision ID: 71d718ed5cd5
Revises: ed08fb0c8a42
Create Date: 2026-06-10 21:14:41.740749

Schema changes:
  1. Add ForeignKey constraint on memories.related_memory_id → memories.id
     (ondelete SET NULL) — ensures referential integrity and cascading cleanup.
  2. Add users.last_sleep_notification_date (String(10), nullable) —
     persists the date of last sleep notification across bot restarts,
     preventing duplicate sleep/wake-up alerts.

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "71d718ed5cd5"
down_revision: Union[str, Sequence[str], None] = "ed08fb0c8a42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add FK on memories.related_memory_id + last_sleep_notification_date on users."""
    # 1. ForeignKey on memories.related_memory_id → memories.id
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_memories_related_memory_id",
            "memories",
            ["related_memory_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # 2. Persist last sleep notification date on users
    #    Check if column already exists — the initial migration uses
    #    Base.metadata.create_all() which includes all current ORM columns,
    #    so the column may already be present on fresh databases.
    conn = op.get_bind()
    result = conn.execute(sa.text("PRAGMA table_info(users)"))
    existing_columns = {row[1] for row in result}
    if "last_sleep_notification_date" not in existing_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "last_sleep_notification_date",
                    sa.String(length=10),
                    nullable=True,
                )
            )


def downgrade() -> None:
    """Remove FK and drop last_sleep_notification_date column."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("last_sleep_notification_date")

    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.drop_constraint("fk_memories_related_memory_id", type_="foreignkey")
