"""Timer model — persistent timer metadata for crash recovery."""

from __future__ import annotations

from sqlalchemy import BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models._base import Base


class Timer(Base):
    """Timer metadata.

    ponytail: global timer namespace (no user_id). The tool is currently
    single-user/owner-only. Add user_id + migration if multi-user isolation
    is ever required.
    """

    __tablename__ = "timers"
    __table_args__ = {"keep_existing": True}

    timer_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    fire_at: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False, default="")
