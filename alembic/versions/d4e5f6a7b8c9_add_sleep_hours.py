"""Add sleep_start_hour and sleep_end_hour to user_settings.

Revision ID: d4e5f6a7b8c9
Revises: z9y8x7w6v5u4
Create Date: 2026-06-23 18:00:00

"""

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if columns already exist (SQLAlchemy may auto-create via ORM)
    conn = op.get_bind()
    result = conn.execute(sa.text("PRAGMA table_info(user_settings)"))
    existing_cols = {row[1] for row in result}
    if "sleep_start_hour" not in existing_cols:
        op.add_column(
            "user_settings",
            sa.Column(
                "sleep_start_hour", sa.Integer(), nullable=True, server_default="23"
            ),
        )
    if "sleep_end_hour" not in existing_cols:
        op.add_column(
            "user_settings",
            sa.Column(
                "sleep_end_hour", sa.Integer(), nullable=True, server_default="7"
            ),
        )


def downgrade() -> None:
    op.drop_column("user_settings", "sleep_end_hour")
    op.drop_column("user_settings", "sleep_start_hour")
