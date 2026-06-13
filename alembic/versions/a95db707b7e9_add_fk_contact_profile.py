"""add_fk_contact_profile

Revision ID: a95db707b7e9
Revises: 71d718ed5cd5
Create Date: 2026-06-10 23:18:22.744775

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a95db707b7e9"
down_revision: Union[str, Sequence[str], None] = "71d718ed5cd5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ForeignKey constraint on contact_profiles.contact_id → contacts.id."""
    with op.batch_alter_table("contact_profiles", schema=None) as batch_op:
        # SQLite requires batch mode for ALTER TABLE.
        # The ORM model changed from BigInteger to Integer (matching contacts.id type).
        batch_op.alter_column(
            "contact_id",
            existing_type=sa.BIGINT(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
        batch_op.create_foreign_key(
            "fk_contact_profiles_contact_id_contacts",
            "contacts",
            ["contact_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """Remove ForeignKey constraint, revert type to BigInteger."""
    with op.batch_alter_table("contact_profiles", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_contact_profiles_contact_id_contacts", type_="foreignkey"
        )
        batch_op.alter_column(
            "contact_id",
            existing_type=sa.Integer(),
            type_=sa.BIGINT(),
            existing_nullable=False,
        )
