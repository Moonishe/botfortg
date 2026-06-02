"""merge scheduled_messages with main branch

Revision ID: fb56dd543d87
Revises: s1t2u3v4w5x6, e1d7c0f3ac9c
Create Date: 2026-05-30 17:48:23.845190

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb56dd543d87'
down_revision: Union[str, Sequence[str], None] = ('s1t2u3v4w5x6', 'e1d7c0f3ac9c')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
