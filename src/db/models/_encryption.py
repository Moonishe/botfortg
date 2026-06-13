"""ORM-модель: таблица encryption_keys для KEK/DEK ротации.

Хранит DEK'и, зашифрованные KEK (Fernet-токены).
KEK НИКОГДА не хранится в БД — только в .env.
"""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models._base import Base


class EncryptionKey(Base):
    """DEK, зашифрованный KEK и сохранённый в БД.

    Таблица: encryption_keys
    """

    __tablename__ = "encryption_keys"

    key_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    encrypted_dek: Mapped[str] = mapped_column(
        Text, nullable=False, comment="DEK зашифрованный KEK (Fernet-токен)"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
        comment="Является ли этот DEK текущим активным",
    )
    created_at: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=lambda: datetime.now(UTC).isoformat(),
        comment="ISO8601 timestamp создания",
    )
    rotated_at: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="ISO8601 timestamp последней ротации (для неактивных — когда заменён)",
    )
