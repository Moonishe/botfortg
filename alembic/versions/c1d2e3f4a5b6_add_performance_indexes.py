"""add_performance_indexes

Revision ID: c1d2e3f4a5b6
Revises: a8b9c0d1e2f3
Create Date: 2026-06-11 17:00:00.000000

Добавляет производительные индексы и уникальные ограничения
для устранения full-table-scan при JOIN-ах и предотвращения дубликатов.
Все операции — через PRAGMA guard для идемпотентности.

Bug 1-3:  Missing indexes on BigInteger FKs
Bug 4:    Missing UniqueConstraint on contact_profiles
Bug 5:    Missing indexes on frequently filtered columns
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    """Проверить существование таблицы через sa.inspect (SQLite)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    """Проверить существование колонки через PRAGMA table_info (SQLite)."""
    if not _table_exists(table_name):
        return False
    conn = op.get_bind()
    result = conn.execute(sa.text(f"PRAGMA table_info({table_name})"))
    return any(row[1] == column_name for row in result)


def _index_exists(index_name: str, table_name: str) -> bool:
    """Проверить наличие индекса через PRAGMA index_list (SQLite)."""
    if not _table_exists(table_name):
        return False  # таблицы нет → индекса нет и не нужно создавать
    conn = op.get_bind()
    result = conn.execute(sa.text(f"PRAGMA index_list({table_name})"))
    return any(row[1] == index_name for row in result)


def upgrade() -> None:
    """Добавить индексы производительности и уникальные ограничения."""

    # ─── Bug 1-3: Missing indexes on BigInteger FKs ───

    if _table_exists("monitor_rules") and not _index_exists(
        "ix_monitor_rules_user_id", "monitor_rules"
    ):
        op.create_index(
            "ix_monitor_rules_user_id",
            "monitor_rules",
            ["user_id"],
            unique=False,
        )

    if _table_exists("monitor_alerts") and not _index_exists(
        "ix_monitor_alerts_user_id", "monitor_alerts"
    ):
        op.create_index(
            "ix_monitor_alerts_user_id",
            "monitor_alerts",
            ["user_id"],
            unique=False,
        )

    if _table_exists("message_reactions") and not _index_exists(
        "ix_message_reactions_user_id", "message_reactions"
    ):
        op.create_index(
            "ix_message_reactions_user_id",
            "message_reactions",
            ["user_id"],
            unique=False,
        )

    # ─── Bug 4: UniqueConstraint on contact_profiles ───

    if _table_exists("contact_profiles") and not _index_exists(
        "uq_contact_profile_user_contact", "contact_profiles"
    ):
        op.create_index(
            "uq_contact_profile_user_contact",
            "contact_profiles",
            ["user_id", "contact_id"],
            unique=True,
        )

    # ─── Bug 5: Composite indexes on frequently filtered columns ───

    if _table_exists("monitored_sources") and not _index_exists(
        "ix_monitored_sources_active_type", "monitored_sources"
    ):
        op.create_index(
            "ix_monitored_sources_active_type",
            "monitored_sources",
            ["is_active", "entity_type"],
            unique=False,
        )

    if _table_exists("monitor_rules") and not _index_exists(
        "ix_monitor_rules_is_active", "monitor_rules"
    ):
        op.create_index(
            "ix_monitor_rules_is_active",
            "monitor_rules",
            ["is_active"],
            unique=False,
        )

    if _table_exists("llm_key_slots") and not _index_exists(
        "ix_llm_key_slots_lookup", "llm_key_slots"
    ):
        op.create_index(
            "ix_llm_key_slots_lookup",
            "llm_key_slots",
            ["provider", "purpose", "enabled"],
            unique=False,
        )

    if _table_exists("scheduled_messages") and not _index_exists(
        "ix_scheduled_messages_status_send", "scheduled_messages"
    ):
        op.create_index(
            "ix_scheduled_messages_status_send",
            "scheduled_messages",
            ["status", "send_at"],
            unique=False,
        )

    if (
        _table_exists("pending_questions")
        and _column_exists("pending_questions", "expires_at")
        and not _index_exists("ix_pending_questions_expires_at", "pending_questions")
    ):
        op.create_index(
            "ix_pending_questions_expires_at",
            "pending_questions",
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    """Удалить добавленные индексы и уникальное ограничение."""

    # pending_questions: индекс только если колонка expires_at существует
    if _column_exists("pending_questions", "expires_at"):
        op.drop_index("ix_pending_questions_expires_at", table_name="pending_questions")
    if _table_exists("scheduled_messages"):
        op.drop_index(
            "ix_scheduled_messages_status_send", table_name="scheduled_messages"
        )
    if _table_exists("llm_key_slots"):
        op.drop_index("ix_llm_key_slots_lookup", table_name="llm_key_slots")
    if _table_exists("monitor_rules"):
        op.drop_index("ix_monitor_rules_is_active", table_name="monitor_rules")
    if _table_exists("monitored_sources"):
        op.drop_index(
            "ix_monitored_sources_active_type", table_name="monitored_sources"
        )
    if _table_exists("contact_profiles"):
        op.drop_index("uq_contact_profile_user_contact", table_name="contact_profiles")
    if _table_exists("message_reactions"):
        op.drop_index("ix_message_reactions_user_id", table_name="message_reactions")
    if _table_exists("monitor_alerts"):
        op.drop_index("ix_monitor_alerts_user_id", table_name="monitor_alerts")
    if _table_exists("monitor_rules"):
        op.drop_index("ix_monitor_rules_user_id", table_name="monitor_rules")
