"""agent_cache composite pk

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-21 15:01:00.000000

Добавляет user_id FK и составной PK (user_id, cache_key) в таблицу agent_cache.
Идемпотентна: если init_db уже создал таблицу из обновлённой ORM-модели — no-op.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add user_id FK + composite PK to agent_cache. Idempotent."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = [c["name"] for c in inspector.get_columns("agent_cache")]
    pk = inspector.get_pk_constraint("agent_cache")
    pk_cols = pk.get("constrained_columns", [])

    # Already migrated (init_db created it from updated ORM model) — no-op
    if "user_id" in columns and "user_id" in pk_cols:
        return

    # Add user_id column if missing
    if "user_id" not in columns:
        with op.batch_alter_table("agent_cache") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "user_id", sa.BigInteger(), nullable=False, server_default="0"
                )
            )

    # Recreate PK as composite — batch mode handles SQLite table copy
    with op.batch_alter_table("agent_cache", recreate="always") as batch_op:
        batch_op.create_primary_key("agent_cache_pkey", ["user_id", "cache_key"])

    # Add FK constraint (best-effort — SQLite may not support ALTER ADD FK)
    fks = [fk.get("name") for fk in inspector.get_foreign_keys("agent_cache")]
    if "agent_cache_user_id_fkey" not in fks:
        try:
            op.create_foreign_key(
                "agent_cache_user_id_fkey",
                "agent_cache",
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )
        except Exception:
            pass  # SQLite may not support ALTER TABLE ADD FK


def downgrade() -> None:
    """Revert agent_cache to single PK on cache_key."""
    with op.batch_alter_table("agent_cache", recreate="always") as batch_op:
        try:
            batch_op.drop_constraint("agent_cache_user_id_fkey", type_="foreignkey")
        except Exception:
            pass
        batch_op.create_primary_key("agent_cache_pkey", ["cache_key"])
        batch_op.drop_column("user_id")
