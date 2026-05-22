"""SQLite persistence for embedding cache — survives restarts."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models._base import Base


class EmbeddingCacheEntry(Base):
    __tablename__ = "embedding_cache"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    text_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    model: Mapped[str] = mapped_column(String(64), default="")
    embedding_json: Mapped[str] = mapped_column(Text)  # JSON-serialized list[float]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
