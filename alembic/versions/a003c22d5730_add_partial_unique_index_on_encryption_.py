"""add partial unique index on encryption_keys.is_active

Revision ID: a003c22d5730
Revises: a1b2c3d4e5f6
Create Date: 2026-06-19 22:10:44.246735

Partial unique index ``ix_encryption_keys_active`` гарантирует,
что только одна запись в ``encryption_keys`` может иметь ``is_active=1``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a003c22d5730"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    """Идемпотентная проверка существования таблицы."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def _index_exists(index_name: str, table_name: str) -> bool:
    """Идемпотентная проверка существования индекса."""
    if not _table_exists(table_name):
        return False
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    """Создаёт partial unique index на encryption_keys.is_active."""
    if _table_exists("encryption_keys") and not _index_exists(
        "ix_encryption_keys_active", "encryption_keys"
    ):
        # Перед созданием unique-индекса чистим дубликаты active-записей:
        # если есть >1 строк с is_active=1, оставляем только с минимальным key_id.
        conn = op.get_bind()
        dup_count = conn.execute(
            sa.text("SELECT COUNT(*) FROM encryption_keys WHERE is_active = 1")
        ).scalar()
        if dup_count and dup_count > 1:
            conn.execute(
                sa.text(
                    """
                    DELETE FROM encryption_keys
                    WHERE key_id NOT IN (
                        SELECT MIN(key_id) FROM encryption_keys
                        WHERE is_active = 1
                    )
                    AND is_active = 1
                    """
                )
            )

        op.create_index(
            "ix_encryption_keys_active",
            "encryption_keys",
            ["is_active"],
            unique=True,
            sqlite_where=sa.text("is_active = 1"),
        )


def downgrade() -> None:
    """Удаляет partial unique index с encryption_keys."""
    if _index_exists("ix_encryption_keys_active", "encryption_keys"):
        op.drop_index("ix_encryption_keys_active", table_name="encryption_keys")
