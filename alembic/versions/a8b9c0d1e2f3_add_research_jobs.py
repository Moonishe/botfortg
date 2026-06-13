"""add_research_jobs

Revision ID: a8b9c0d1e2f3
Revises: b1c2d3e4f5a6
Create Date: 2026-06-11 14:00:00.000000

Добавляет таблицу ``research_jobs`` для персистентного хранения
задач Deep Research Pipeline. Дублирует in-memory ``_jobs: dict``
для выживаемости при рестарте.

PRAGMA guard: все операции проверяют существование таблицы/колонок
через ``sa.inspect()``, поэтому миграция идемпотентна — безопасна
для повторного запуска.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать таблицу research_jobs с PRAGMA guard."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Проверяем, существует ли таблица (идемпотентность)
    existing_tables = inspector.get_table_names()
    if "research_jobs" in existing_tables:
        # Таблица уже существует — проверяем колонки
        existing_cols = {c["name"] for c in inspector.get_columns("research_jobs")}
        if "job_id" in existing_cols:
            return  # Таблица полностью создана, пропускаем

    # Создаём таблицу целиком
    op.create_table(
        "research_jobs",
        sa.Column(
            "job_id",
            sa.String(length=32),
            primary_key=True,
            comment="12-символьный hex ID (первые 12 байт UUID4)",
        ),
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            nullable=True,
            index=True,
            comment="ID владельца (users.telegram_id)",
        ),
        sa.Column(
            "query",
            sa.Text(),
            nullable=False,
            comment="Оригинальный поисковый запрос",
        ),
        sa.Column(
            "max_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
            comment="Максимальное время выполнения в минутах",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
            comment="Статус: pending/needs_clarification/phase1_running/phase2_running/completed/failed",
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=True,
            comment="Сводный Markdown-отчёт",
        ),
        sa.Column(
            "sources_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Количество собранных источников",
        ),
        sa.Column(
            "topics_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Количество тем/подтем",
        ),
        sa.Column(
            "clarify_question",
            sa.Text(),
            nullable=True,
            comment="Уточняющий вопрос (если NEEDS_CLARIFICATION)",
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="Сообщение об ошибке (если FAILED)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="Время создания задачи (UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="Время последнего обновления (UTC)",
        ),
    )

    # Индекс для быстрого поиска по статусу + updated_at (cleanup)
    op.create_index(
        "ix_research_jobs_status_updated",
        "research_jobs",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    """Удалить таблицу research_jobs."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_tables = inspector.get_table_names()
    if "research_jobs" in existing_tables:
        op.drop_index("ix_research_jobs_status_updated", table_name="research_jobs")
        op.drop_table("research_jobs")
