"""State snapshot — CRIU-style capture/restore of volatile in-memory state."""

from src.core.state.snapshot_engine import SnapshotEngine, snapshot_engine

__all__ = ["SnapshotEngine", "snapshot_engine"]
