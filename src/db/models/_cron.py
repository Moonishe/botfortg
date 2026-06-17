"""CronJob — гибкая модель для повторяющихся задач по расписанию."""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ._base import Base, User


class CronJob(Base):
    """Повторяющаяся задача, выполняемая по cron-расписанию.

    Attributes:
        id: Уникальный идентификатор задачи.
        user_id: Владелец задачи (FK -> users.id).
        name: Человеко-читаемое название задачи.
        description: Описание задачи / что делает.
        cron_expression: Cron-выражение (5-польное: '*/5 * * * *').
        timezone: IANA-таймзона (по умолчанию UTC). В этом часовом поясе
            интерпретируется cron_expression.
        enabled: Активна ли задача.
        payload_type: Тип действия: 'message' | 'llm_prompt' | 'webhook' | 'callback'.
        payload: JSON-словарь с параметрами действия.
            Для 'message': {"text": "...", "chat_id": 123}.
            Для 'llm_prompt': {"prompt": "...", "context": "..."}.
        channel: Канал доставки: 'telegram' | 'userbot' | 'notification_queue'.
        notify_on_error: Отправлять ли уведомление при ошибке выполнения.
        max_runs: Максимальное количество выполнений (0 = без ограничения).
        run_count: Сколько раз уже выполнена.
        max_run_date: Максимальная дата выполнения (None = без ограничения).
        last_run_at: Время последнего выполнения (UTC).
        next_run_at: Время следующего выполнения (UTC) — пересчитывается
            после каждого выполнения через croniter.
        created_at: Время создания.
        updated_at: Время последнего обновления.
        tags: JSON-список тегов для категоризации (blueprint, custom, etc).
    """

    __tablename__ = "cron_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)  # Integer — SQLite autoincrement
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cron_expression: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="5-польное cron-выражение"
    )
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    payload_type: Mapped[str] = mapped_column(
        String(32),
        default="message",
        comment="message | llm_prompt | webhook | callback",
    )
    payload: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON-параметры действия"
    )

    channel: Mapped[str] = mapped_column(
        String(32),
        default="notification_queue",
        comment="telegram | userbot | notification_queue",
    )
    notify_on_error: Mapped[bool] = mapped_column(Boolean, default=True)

    max_runs: Mapped[int] = mapped_column(Integer, default=0, comment="0 = без лимита")
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    max_run_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    tags: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON-список тегов"
    )

    user: Mapped[User] = relationship(back_populates="cron_jobs")
