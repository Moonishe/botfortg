"""add pending_questions FK and encryption datetime

Revision ID: 01d9cbf0c45e
Revises: 7810dc27b574
Create Date: 2026-06-16 22:23:19.570006

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "01d9cbf0c45e"
down_revision: Union[str, Sequence[str], None] = "7810dc27b574"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. pending_questions: add expires_at and FK owner_id -> users.id
    with op.batch_alter_table("pending_questions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "expires_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(datetime('now', '+24 hours'))"),
                nullable=False,
            )
        )
        batch_op.create_foreign_key(
            "fk_pending_questions_owner_id_users",
            "users",
            ["owner_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # 2. encryption_keys: store timestamps as timezone-aware DateTime
    with op.batch_alter_table("encryption_keys", schema=None) as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.VARCHAR(length=32),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "rotated_at",
            existing_type=sa.VARCHAR(length=32),
            type_=sa.DateTime(timezone=True),
            existing_nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    # 1. encryption_keys: revert to VARCHAR timestamps
    with op.batch_alter_table("encryption_keys", schema=None) as batch_op:
        batch_op.alter_column(
            "rotated_at",
            existing_type=sa.DateTime(timezone=True),
            type_=sa.VARCHAR(length=32),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            type_=sa.VARCHAR(length=32),
            existing_nullable=False,
        )

    # 2. pending_questions: drop FK and expires_at
    with op.batch_alter_table("pending_questions", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_pending_questions_owner_id_users", type_="foreignkey"
        )
        batch_op.drop_column("expires_at")
