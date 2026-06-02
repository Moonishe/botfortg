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
    """Upgrade schema — create all ORM tables from current models.

    SAFETY: If the database already has tables (e.g. persistent volume on
    Railway with a previously deployed schema), skip create_all() entirely
    to avoid deadlocking SQLite or triggering model imports that clash.
    This migration is idempotent — it only materialises tables on a truly
    fresh (empty) database.
    """
    # Ensure src/ is on sys.path so we can import models
    _root = Path(__file__).resolve().parent.parent
    if str(_root / "src") not in sys.path:
        sys.path.insert(0, str(_root / "src"))

    _bind = op.get_bind()
    _existing = _bind.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version' LIMIT 1"
    ).fetchone()

    if _existing is not None:
        # Tables already exist — database was seeded by a previous deploy.
        # Skip create_all() to avoid import side-effects and deadlocks.
        import logging

        logging.getLogger("alembic").info(
            "Skipping initial_schema.create_all — DB already has table %r", _existing[0]
        )
        return

    from db.models import Base as _Base

    _Base.metadata.create_all(bind=_bind)


def downgrade() -> None:
    """Downgrade schema — drop all ORM tables."""
    _root = Path(__file__).resolve().parent.parent
    if str(_root / "src") not in sys.path:
        sys.path.insert(0, str(_root / "src"))

    from db.models import Base as _Base

    _Base.metadata.drop_all(bind=op.get_bind())
