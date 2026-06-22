"""change user id to bigint with variant

Revision ID: b2c3d4e5f6a7
Revises: a003c22d5730
Create Date: 2026-06-21 15:00:00.000000

Изменяет тип User.id на BigInteger, но с SQLite-вариантом Integer
(для автоинкремента). На SQLite эта миграция — no-op (INTEGER и BIGINT
эквивалентны). На PostgreSQL выполняет ALTER COLUMN id TYPE BIGINT.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a003c22d5730"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Alter users.id to BIGINT (no-op on SQLite)."""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column("users", "id", type_=sa.BigInteger())


def downgrade() -> None:
    """Revert users.id to INTEGER (no-op on SQLite)."""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column("users", "id", type_=sa.Integer())
