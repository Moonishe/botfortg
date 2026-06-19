"""normalize soul snapshot created_at timezone

Revision ID: 7b7814daa545
Revises: 4003d4e0335c
Create Date: 2026-06-18 01:15:09.680852

"""

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7b7814daa545"
down_revision: str | Sequence[str] | None = "4003d4e0335c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Normalize naive datetimes to UTC-aware: append +00:00 offset.
    # Skip rows that already carry a timezone offset (positive OR negative).
    # GLOB '*-[0-9][0-9]:[0-9][0-9]' catches negative offsets like -05:00
    # without matching the date portion (YYYY-MM-DD), because date segments
    # are followed by '-' or ' ', not ':'.
    op.execute(
        "UPDATE soul_snapshots SET created_at = created_at || '+00:00' "
        "WHERE created_at IS NOT NULL "
        "AND created_at NOT LIKE '%+%' "
        "AND created_at NOT GLOB '*-[0-9][0-9]:[0-9][0-9]'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Reversal is lossy — strip the timezone offset
    pass
