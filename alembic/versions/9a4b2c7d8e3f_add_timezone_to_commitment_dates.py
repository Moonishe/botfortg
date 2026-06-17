"""add_timezone_to_commitment_dates

Placeholder migration: timezone awareness for commitment dates was
implemented later via the ORM model definitions in the initial migration.
This revision exists only to preserve the Alembic branch history for the
add_smart_cache migration that follows it.

Revision ID: 9a4b2c7d8e3f
Revises: fe658c1e6a41
Create Date: 2026-05-22 12:00:00.000000

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "9a4b2c7d8e3f"
down_revision: Union[str, Sequence[str], None] = "fe658c1e6a41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op placeholder: schema already reflects the model definitions."""
    pass


def downgrade() -> None:
    """No-op placeholder."""
    pass
