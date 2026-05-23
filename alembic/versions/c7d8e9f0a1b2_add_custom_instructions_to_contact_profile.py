"""add custom_instructions to contact_profile

Revision ID: c7d8e9f0a1b2
Revises: f1a2b3c4d5e6
Create Date: 2026-05-23 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add custom_instructions column to contact_profiles."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = {c["name"] for c in inspector.get_columns("contact_profiles")}
    if "custom_instructions" not in cols:
        op.add_column(
            "contact_profiles",
            sa.Column("custom_instructions", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Downgrade schema — drop custom_instructions column."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = {c["name"] for c in inspector.get_columns("contact_profiles")}
    if "custom_instructions" in cols:
        op.drop_column("contact_profiles", "custom_instructions")
