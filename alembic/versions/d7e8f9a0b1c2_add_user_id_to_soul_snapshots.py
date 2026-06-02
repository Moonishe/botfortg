"""add_user_id_to_soul_snapshots

Revision ID: d7e8f9a0b1c2
Revises: c6c5965acc9d
Create Date: 2026-05-31 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c6c5965acc9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {col["name"] for col in inspector.get_columns("soul_snapshots")}

    if "user_id" in columns:
        return

    # SQLite has three ALTER TABLE limitations we must work around:
    # 1. ``ALTER TABLE ... ADD COLUMN ... NOT NULL`` requires a LITERAL
    #    default — subqueries / expressions are not allowed.
    # 2. There is no ``ALTER TABLE ... ALTER COLUMN ... SET NOT NULL``;
    #    the only way to flip a column to NOT NULL is to rebuild the table.
    # 3. The SQLite dialect refuses to add a column with a FOREIGN KEY
    #    constraint via ``ALTER TABLE ADD COLUMN``.
    # We therefore use ``batch_alter_table`` (copy-and-move strategy) in
    # two passes:
    #   Pass 1 — add the column (nullable, with a literal default of 0 so
    #            the rebuilt temp table doesn't reject the COPY of
    #            existing rows).  After the rebuild, backfill from the
    #            first user.
    #   Pass 2 — flip the column to NOT NULL and drop the literal default
    #            in a second rebuild.
    # Two rebuilds is the simplest way to keep the FK, the index, and the
    # NOT NULL semantics in place on SQLite without raw-SQL DDL.

    with op.batch_alter_table("soul_snapshots") as batch_op:
        batch_op.add_column(
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey(
                    "users.id",
                    ondelete="CASCADE",
                    name="fk_soul_snapshots_user_id_users",
                ),
                nullable=True,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_index(
            op.f("ix_soul_snapshots_user_id"),
            ["user_id"],
        )

    # Backfill from the first user (if any).  ``default_user_id`` is a
    # server-trusted integer pulled from the DB itself, so direct string
    # interpolation is safe (no SQL injection risk).
    first_user = conn.execute(sa.text("SELECT id FROM users LIMIT 1")).first()
    if first_user is not None:
        default_user_id = int(first_user[0])
        op.execute(
            "UPDATE soul_snapshots SET user_id = {} "
            "WHERE user_id = 0 OR user_id IS NULL".format(default_user_id)
        )

    # Second pass: flip the column to NOT NULL and drop the literal default.
    with op.batch_alter_table("soul_snapshots") as batch_op:
        batch_op.alter_column(
            "user_id",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {col["name"] for col in inspector.get_columns("soul_snapshots")}

    if "user_id" not in columns:
        return

    # Drop the column via ``batch_alter_table`` so the FK and the index
    # are removed in a single table rebuild.  Plain ``ALTER TABLE DROP
    # COLUMN`` is not supported on SQLite when a FOREIGN KEY references
    # the column.
    with op.batch_alter_table("soul_snapshots") as batch_op:
        batch_op.drop_index(
            op.f("ix_soul_snapshots_user_id"),
        )
        batch_op.drop_column("user_id")
