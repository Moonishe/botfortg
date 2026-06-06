"""add_llm_key_slot_models — multi-model support per key slot.

Revision ID: m1n2o3p4q5r6
Revises: 6c81883d69f4
Create Date: 2026-06-06 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "m1n2o3p4q5r6"
down_revision: Union[str, Sequence[str], None] = "70f3b0b35097"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавляет таблицу llm_key_slot_models для мульти-модельного выбора."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "llm_key_slot_models" in inspector.get_table_names():
        return
    op.create_table(
        "llm_key_slot_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "slot_id",
            sa.Integer(),
            sa.ForeignKey("llm_key_slots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(datetime('now'))"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_key_slot_models_slot_id",
        "llm_key_slot_models",
        ["slot_id"],
    )


def downgrade() -> None:
    """Удаляет таблицу llm_key_slot_models."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "llm_key_slot_models" not in inspector.get_table_names():
        return
    op.drop_index("ix_llm_key_slot_models_slot_id", table_name="llm_key_slot_models")
    op.drop_table("llm_key_slot_models")
