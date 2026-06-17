"""Shared SQLAlchemy filters for memory compaction/sweep operations."""

from __future__ import annotations

from sqlalchemy import or_

from src.db.models import Memory


def non_task_memory_type_filter():
    """Filter for memory rows that are NOT tasks (includes NULL memory_type)."""
    return or_(
        Memory.memory_type != "task",
        Memory.memory_type.is_(None),
    )
