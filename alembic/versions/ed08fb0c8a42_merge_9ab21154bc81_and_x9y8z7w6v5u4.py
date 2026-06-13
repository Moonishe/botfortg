"""merge_9ab21154bc81_and_x9y8z7w6v5u4

Revision ID: ed08fb0c8a42
Revises: 9ab21154bc81, x9y8z7w6v5u4
Create Date: 2026-06-10 21:14:20.732404

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ed08fb0c8a42"
down_revision: Union[str, Sequence[str], None] = ("9ab21154bc81", "x9y8z7w6v5u4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge revision: no schema changes needed.

    This revision exists solely to combine two divergent heads:
      - 9ab21154bc81: merged monitor tables into main (merge of m1n2o3p4q5r6 + m5n6o7p8q9r0)
      - x9y8z7w6v5u4: added UniqueConstraint on monitor_alerts (rule_id, message_id)

    The actual schema changes are in the parent revisions.
    """
    pass


def downgrade() -> None:
    """Merge revision: no schema changes — downgrade is handled by parent revisions."""
    pass
