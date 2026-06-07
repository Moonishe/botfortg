"""Add monitor tables: monitored_sources, monitor_rules, monitored_messages, monitor_alerts

Revision ID: m5n6o7p8q9r0
Revises: p3q4r5s6t7u8
Create Date: 2026-06-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "m5n6o7p8q9r0"
down_revision: Union[str, Sequence[str], None] = "p3q4r5s6t7u8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    # ── monitored_sources ──
    if "monitored_sources" not in existing:
        op.create_table(
            "monitored_sources",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("entity_id", sa.BigInteger(), nullable=False),
            sa.Column("entity_type", sa.String(16), nullable=False),
            sa.Column("title", sa.String(256), nullable=True),
            sa.Column("username", sa.String(128), nullable=True),
            sa.Column("access_hash", sa.BigInteger(), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("1")),
            sa.Column(
                "added_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_message_id", sa.BigInteger(), server_default=sa.text("0")),
            sa.Column("settings", sa.JSON(), server_default=sa.text("'{}'")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_monitored_sources_user_id", "monitored_sources", ["user_id"]
        )

    # ── monitor_rules ──
    if "monitor_rules" not in existing:
        op.create_table(
            "monitor_rules",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(128), nullable=True),
            sa.Column("priority", sa.Integer(), server_default=sa.text("0")),
            sa.Column("conditions", sa.JSON(), nullable=False),
            sa.Column("actions", sa.JSON(), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("1")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["source_id"], ["monitored_sources.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    # ── monitored_messages ──
    if "monitored_messages" not in existing:
        op.create_table(
            "monitored_messages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=False),
            sa.Column("message_id", sa.BigInteger(), nullable=False),
            sa.Column("date", sa.DateTime(timezone=True), nullable=False),
            sa.Column("sender_id", sa.BigInteger(), nullable=True),
            sa.Column("sender_name", sa.String(256), nullable=True),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("media_type", sa.String(32), nullable=True),
            sa.Column("entities", sa.JSON(), nullable=True),
            sa.Column("views", sa.Integer(), nullable=True),
            sa.Column("forwards", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(
                ["source_id"], ["monitored_sources.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_monitored_messages_source_msg",
            "monitored_messages",
            ["source_id", "message_id"],
            unique=True,
        )

    # ── monitor_alerts ──
    if "monitor_alerts" not in existing:
        op.create_table(
            "monitor_alerts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("rule_id", sa.Integer(), nullable=True),
            sa.Column("message_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(16), server_default=sa.text("'pending'")),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["rule_id"], ["monitor_rules.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["message_id"], ["monitored_messages.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    if "monitor_alerts" in existing:
        op.drop_table("monitor_alerts")
    if "monitored_messages" in existing:
        op.drop_table("monitored_messages")
    if "monitor_rules" in existing:
        op.drop_table("monitor_rules")
    if "monitored_sources" in existing:
        op.drop_table("monitored_sources")
