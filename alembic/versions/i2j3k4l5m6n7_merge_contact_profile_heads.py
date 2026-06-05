"""Merge contact profile migration heads.

Revision ID: i2j3k4l5m6n7
Revises: c7d8e9f0a1b2, h1i2j3k4l5m6
Create Date: 2026-05-24

Note: e1d7c0f3ac9c was removed from parents (was added in 6e591f9 to fix
"MultipleHeads") because it created a CYCLE in the migration graph:

    c6c5965acc9d -> e1d7c0f3ac9c -> i2j3k4l5m6n7 -> n2o3p4q5r6s7 -> ...
    -> r1s2t3u4v5w6 -> c6c5965acc9d (closes the loop)

Removing it from i2j3k4l5m6n7 breaks the cycle while keeping a single head
(70f3b0b35097), which is the only one reachable from both merge branches
via the chain r1s2t3u4v5w6 -> s1t2u3v4w5x6 -> fb56dd543d87 and the
e1d7c0f3ac9c -> fb56dd543d87 sibling edge.
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "i2j3k4l5m6n7"
down_revision: Union[str, Sequence[str], None] = (
    "c7d8e9f0a1b2",
    "h1i2j3k4l5m6",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge-only migration; both parent revisions contain the schema changes."""


def downgrade() -> None:
    """Merge-only migration; downgrade is handled by parent revisions."""
