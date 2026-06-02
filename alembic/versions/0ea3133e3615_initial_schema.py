"""initial_schema

Revision ID: 0ea3133e3615
Revises:
Create Date: 2026-05-20 23:17:22.251602

Why NOT empty anymore
---------------------
Previous version was empty, causing "no such table" errors on fresh DBs
when downstream migrations (318404aba419, 6c81883d69f4, etc.) tried to
ALTER TABLE on tables that didn't exist.

Now calls ``Base.metadata.create_all(bind=op.get_bind())`` to stamp ALL
ORM tables from the current model definitions in one shot.

FTS5 virtual tables (messages_fts*, memories_fts*) are excluded from
Alembic via ``include_object`` in env.py — they are managed by raw SQL
in init_db().

Workflow for future schema changes
-----------------------------------
1. Edit the ORM model in models.py
2. Generate a migration:
       alembic revision --autogenerate -m "description"
3. Review and apply:
       alembic upgrade head
4. Deploy — init_db() calls ``alembic upgrade head`` automatically.
"""

from typing import Sequence, Union

import sys
from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0ea3133e3615"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Initial schema — intentionally a no-op.

    Table creation is handled by ``init_db()`` in ``src/db/session.py``
    which runs *after* alembic, with all imports already resolved.  Calling
    ``Base.metadata.create_all()`` here causes a deadlock on persistent
    volumes (Railway) because:

    1. Importing ``db.models`` inside an alembic migration triggers
       model-import side-effects that open a *second* SQLite connection
       to the same file.
    2. The second connection blocks on the first (alembic's) → deadlock.

    This migration exists purely to mark the initial alembic version so
    downstream revisions (column additions etc.) can assume the tables
    already exist (they were created by a prior deploy or by init_db)."""
    import logging

    logging.getLogger("alembic").info(
        "initial_schema: no-op, tables handled by init_db()"
    )


def downgrade() -> None:
    """Downgrade schema — drop all ORM tables."""
    _root = Path(__file__).resolve().parent.parent
    if str(_root / "src") not in sys.path:
        sys.path.insert(0, str(_root / "src"))

    from db.models import Base as _Base

    _Base.metadata.drop_all(bind=op.get_bind())
