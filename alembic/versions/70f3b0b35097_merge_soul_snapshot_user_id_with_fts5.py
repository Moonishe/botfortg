"""merge soul_snapshot_user_id with fts5

Revision ID: 70f3b0b35097
Revises: d7e8f9a0b1c2, z9y8x7w6v5u4
Create Date: 2026-05-31 22:05:48.931671

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '70f3b0b35097'
down_revision: Union[str, Sequence[str], None] = ('d7e8f9a0b1c2', 'z9y8x7w6v5u4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
