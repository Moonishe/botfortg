"""Add cron_jobs table for Generic Cron Scheduler.

Revision ID: c0r0n0j0b0s
Revises: 08fa76e38a2a
Create Date: 2026-06-14 15:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c0r0n0j0b0s"
down_revision: Union[str, Sequence[str], None] = "08fa76e38a2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create cron_jobs table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "cron_jobs" not in inspector.get_table_names():
        op.create_table(
            "cron_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False, index=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "cron_expression",
                sa.String(64),
                nullable=False,
                comment="5-польное cron-выражение",
            ),
            sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
            sa.Column(
                "enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")
            ),
            sa.Column(
                "payload_type",
                sa.String(32),
                nullable=False,
                server_default="message",
                comment="message | llm_prompt | webhook | callback",
            ),
            sa.Column(
                "payload",
                sa.Text(),
                nullable=True,
                comment="JSON-параметры действия",
            ),
            sa.Column(
                "channel",
                sa.String(32),
                nullable=False,
                server_default="notification_queue",
                comment="telegram | userbot | notification_queue",
            ),
            sa.Column(
                "notify_on_error",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            ),
            sa.Column(
                "max_runs",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
                comment="0 = без лимита",
            ),
            sa.Column(
                "run_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "max_run_date",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "last_run_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "next_run_at",
                sa.DateTime(timezone=True),
                nullable=True,
                index=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("(datetime('now'))"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("(datetime('now'))"),
            ),
            sa.Column(
                "tags",
                sa.Text(),
                nullable=True,
                comment="JSON-список тегов",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    """Drop cron_jobs table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "cron_jobs" in inspector.get_table_names():
        op.drop_table("cron_jobs")
