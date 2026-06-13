"""L1 SQLite cache model — SmartCacheEntry for the 3-level cache system."""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models._base import Base


class SmartCacheEntry(Base):
    __tablename__ = "smart_cache"

    cache_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    cache_value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="unknown")
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    graduated: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # SHA256 hex digest — dedup
