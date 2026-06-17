"""ORM-модель: таблица encryption_keys для KEK/DEK ротации.

Хранит DEK'и, зашифрованные KEK (Fernet-токены).
KEK НИКОГДА не хранится в БД — только в .env.
"""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import Boolean, DateTime, Integer, Text
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        comment="UTC timestamp создания",
    )
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="UTC timestamp последней ротации (для неактивных — когда заменён)",
    )
