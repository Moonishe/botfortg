"""merge monitor tables into main

Revision ID: 9ab21154bc81
Revises: m1n2o3p4q5r6, m5n6o7p8q9r0
Create Date: 2026-06-07 11:17:41.803685

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9ab21154bc81'
down_revision: Union[str, Sequence[str], None] = ('m1n2o3p4q5r6', 'm5n6o7p8q9r0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
