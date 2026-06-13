"""ORM-модель ResearchJob — персистентность задач Deep Research."""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models._base import Base


class ResearchJob(Base):
    """Персистентное хранилище задач глубокого исследования.

    Дублирует in-memory ``_jobs: dict`` для выживаемости при рестарте.
    In-memory — primary (быстрый доступ), БД — second-tier (восстановление).
    """

    __tablename__ = "research_jobs"

    job_id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        comment="12-символьный hex ID (первые 12 байт UUID4)",
    )
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        comment="ID владельца (users.telegram_id)",
    )
    query: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Оригинальный поисковый запрос"
    )
    max_minutes: Mapped[int] = mapped_column(
        Integer, default=5, comment="Максимальное время выполнения в минутах"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        comment="Статус: pending/needs_clarification/phase1_running/phase2_running/completed/failed",
    )
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Сводный Markdown-отчёт"
    )
    sources_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="Количество собранных источников"
    )
    topics_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="Количество тем/подтем"
    )
    clarify_question: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Уточняющий вопрос (если NEEDS_CLARIFICATION)"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Сообщение об ошибке (если FAILED)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="Время создания задачи (UTC)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="Время последнего обновления (UTC)",
    )
