"""Модели мониторинга Telegram-каналов: MonitoredSource, MonitorRule, MonitoredMessage, MonitoredAlert."""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ._base import Base


class MonitoredSource(Base):
    """Источник мониторинга — канал, группа, супергруппа или чат."""

    __tablename__ = "monitored_sources"
    __table_args__ = (
        UniqueConstraint("user_id", "entity_id", name="uq_monitor_source_user_entity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entity_type: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # channel, group, supergroup, chat
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    access_hash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    last_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # {"keywords": [...], "exclude_keywords": [...]}

    # Связи
    rules: Mapped[list[MonitorRule]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    messages: Mapped[list[MonitoredMessage]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="select",
    )


class MonitorRule(Base):
    """Правило фильтрации — набор условий и действий для мониторинга."""

    __tablename__ = "monitor_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("monitored_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    conditions: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )  # {"keywords": [...], "regex": "...", "exclude_keywords": [...]}
    actions: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )  # {"notify": True, "save": True, "llm_summary": True}
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    # Связи
    source: Mapped[MonitoredSource] = relationship(back_populates="rules")
    alerts: Mapped[list[MonitoredAlert]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        lazy="select",
    )


class MonitoredMessage(Base):
    """Сохранённое сообщение из отслеживаемого источника."""

    __tablename__ = "monitored_messages"
    __table_args__ = (
        UniqueConstraint("source_id", "message_id", name="uq_monmsg_source_msgid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("monitored_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entities: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    forwards: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Связи
    source: Mapped[MonitoredSource] = relationship(back_populates="messages")
    alerts: Mapped[list[MonitoredAlert]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        lazy="select",
    )


class MonitoredAlert(Base):
    """Алерт — срабатывание правила на конкретном сообщении."""

    __tablename__ = "monitor_alerts"
    __table_args__ = (
        UniqueConstraint("rule_id", "message_id", name="uq_monitor_alert_rule_msg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("monitor_rules.id", ondelete="CASCADE"),
        nullable=True,
    )
    message_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("monitored_messages.id", ondelete="CASCADE"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # LLM-саммари сообщения

    # Связи
    rule: Mapped[MonitorRule | None] = relationship(back_populates="alerts")
    message: Mapped[MonitoredMessage | None] = relationship(back_populates="alerts")
