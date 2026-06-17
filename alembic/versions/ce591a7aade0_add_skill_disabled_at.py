"""add skill disabled_at

Revision ID: ce591a7aade0
Revises: af87719466fc
Create Date: 2026-06-18 00:38:04.476640

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ce591a7aade0"
down_revision: Union[str, Sequence[str], None] = "af87719466fc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add disabled_at timestamp to skills."""
    with op.batch_alter_table("skills", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    """Drop disabled_at timestamp from skills."""
    with op.batch_alter_table("skills", schema=None) as batch_op:
        batch_op.drop_column("disabled_at")
