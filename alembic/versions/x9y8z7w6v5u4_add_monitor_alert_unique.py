"""Add UniqueConstraint on monitor_alerts (rule_id, message_id)

Revision ID: x9y8z7w6v5u4
Revises: m5n6o7p8q9r0
Create Date: 2026-06-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "x9y8z7w6v5u4"
down_revision: Union[str, Sequence[str], None] = "m5n6o7p8q9r0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет уникальный индекс на (rule_id, message_id) для монитор-алертов."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    if "monitor_alerts" in existing:
        indexes = inspector.get_indexes("monitor_alerts")
        index_names = [idx["name"] for idx in indexes]

        if "uq_monitor_alert_rule_msg" not in index_names:
            # Сначала чистим существующие дубликаты через подзапрос
            # Оставляем только запись с минимальным id для каждой пары (rule_id, message_id)
            op.execute(
                sa.text(
                    """
                    DELETE FROM monitor_alerts
                    WHERE id NOT IN (
                        SELECT MIN(id) FROM monitor_alerts
                        WHERE rule_id IS NOT NULL AND message_id IS NOT NULL
                        GROUP BY rule_id, message_id
                    )
                    AND rule_id IS NOT NULL AND message_id IS NOT NULL
                    """
                )
            )

            op.create_index(
                "uq_monitor_alert_rule_msg",
                "monitor_alerts",
                ["rule_id", "message_id"],
                unique=True,
            )


def downgrade() -> None:
    """Удаляет уникальный индекс с monitor_alerts."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    if "monitor_alerts" in existing:
        indexes = inspector.get_indexes("monitor_alerts")
        index_names = [idx["name"] for idx in indexes]

        if "uq_monitor_alert_rule_msg" in index_names:
            op.drop_index("uq_monitor_alert_rule_msg", table_name="monitor_alerts")
