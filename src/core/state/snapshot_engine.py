"""CRIU-style snapshot engine — capture/restore volatile in-memory state.

Saves critical in-memory state to disk (JSON) so it can be restored
after a graceful or unexpected restart.

Usage:
    engine = SnapshotEngine()
    await engine.capture()  # save to data/snapshot.json
    await engine.restore()  # load from data/snapshot.json on startup
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time as _time_module
from collections import deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_SNAPSHOT_PATH = "data/snapshot.json"
_SNAPSHOT_MAX_AGE_SEC = 3600  # don't restore snapshots older than 1 hour


class SnapshotEngine:
    """Captures and restores volatile in-memory state."""

    async def capture(self) -> dict[str, Any]:
        """Capture current in-memory state. Returns the snapshot dict."""
        snapshot: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": 1,
            "conversation_context": await self._capture_conversation_context(),
            "active_sessions": await self._capture_active_sessions(),
            "pending_questions": await self._capture_pending_questions(),
            "circuit_breakers": await self._capture_circuit_breakers(),
        }
        return snapshot

    async def save_to_disk(self, snapshot: dict[str, Any] | None = None) -> None:
        """Save snapshot to disk (non-blocking via asyncio.to_thread)."""
        if snapshot is None:
            snapshot = await self.capture()

        def _write() -> None:
            os.makedirs(os.path.dirname(_SNAPSHOT_PATH), exist_ok=True)
            with open(_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, default=str)

        await asyncio.to_thread(_write)
        logger.info("Snapshot saved to %s", _SNAPSHOT_PATH)

    async def restore(self) -> bool:
        """Load snapshot from disk and restore in-memory state.

        Returns True if restored, False if no snapshot or too old.
        """
        if not os.path.exists(_SNAPSHOT_PATH):
            return False

        try:

            def _read() -> dict[str, Any]:
                with open(_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)

            snapshot = await asyncio.to_thread(_read)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load snapshot: %s", e)
            return False

        # Check version — skip if format changed
        version = snapshot.get("version", 0)
        if version != 1:
            logger.warning(
                "Snapshot version mismatch (got %s, expected 1), skipping restore",
                version,
            )
            return False

        # Check age
        ts_str = snapshot.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age < 0:
                logger.warning(
                    "Snapshot timestamp is in the future (%.0fs), skipping restore",
                    -age,
                )
                return False
            if age > _SNAPSHOT_MAX_AGE_SEC:
                logger.warning("Snapshot too old (%.0fs), skipping restore", age)
                return False
        except (ValueError, TypeError):
            logger.warning("Snapshot timestamp invalid, skipping restore")
            return False

        # Validate component data types (untrusted JSON from disk)
        def _ensure_dict(data: Any, key: str) -> dict[str, Any]:
            if not isinstance(data, dict):
                logger.warning(
                    "Snapshot component %r is not a dict (got %s), ignoring",
                    key,
                    type(data).__name__,
                )
                return {}
            return data

        # Restore each component
        await self._restore_conversation_context(
            _ensure_dict(snapshot.get("conversation_context"), "conversation_context")
        )
        await self._restore_active_sessions(
            _ensure_dict(snapshot.get("active_sessions"), "active_sessions")
        )
        await self._restore_pending_questions(
            _ensure_dict(snapshot.get("pending_questions"), "pending_questions")
        )
        await self._restore_circuit_breakers(
            _ensure_dict(snapshot.get("circuit_breakers"), "circuit_breakers")
        )

        logger.info("Snapshot restored from %s", _SNAPSHOT_PATH)
        return True

    # ── Capture methods ──

    async def _capture_conversation_context(self) -> dict[str, Any]:
        """Capture conversation context via public capture_state()."""
        try:
            from src.core.memory.conversation_context import capture_state

            return await capture_state()
        except ImportError:
            return {}

    async def _capture_active_sessions(self) -> dict[str, Any]:
        """Capture active sessions via public capture_state()."""
        try:
            from src.core.memory.session_recorder import capture_state

            return await capture_state()
        except ImportError:
            return {}

    async def _capture_pending_questions(self) -> dict[str, Any]:
        """Capture pending questions via public capture_state()."""
        try:
            from src.core.memory.pending_questions import capture_state

            return await capture_state()
        except ImportError:
            return {}

    async def _capture_circuit_breakers(self) -> dict[str, Any]:
        """Capture ToolCircuitBreaker state via public capture_state() method."""
        try:
            from src.core.actions.tool_middleware import ToolCircuitBreaker
        except ImportError:
            return {}
        return await ToolCircuitBreaker.capture_state()

    # ── Restore methods ──

    async def _restore_conversation_context(self, data: dict[str, Any]) -> None:
        """Restore conversation context via public restore_state()."""
        if not data:
            return
        try:
            from src.core.memory.conversation_context import restore_state

            await restore_state(data)
        except ImportError:
            return

    async def _restore_active_sessions(self, data: dict[str, Any]) -> None:
        """Restore active sessions via public restore_state()."""
        if not data:
            return
        try:
            from src.core.memory.session_recorder import restore_state

            await restore_state(data)
        except ImportError:
            return

    async def _restore_pending_questions(self, data: dict[str, Any]) -> None:
        """Restore pending questions via public restore_state()."""
        if not data:
            return
        try:
            from src.core.memory.pending_questions import restore_state

            await restore_state(data)
        except ImportError:
            return

    async def _restore_circuit_breakers(self, data: dict[str, Any]) -> None:
        """Restore ToolCircuitBreaker state via public restore_state() method.

        Pre-processes cooldown expiry (OPEN → HALF_OPEN), clamps failures,
        and preserves ``_probe_in_flight`` before delegating to the public API.
        """
        if not data:
            return
        try:
            from src.core.actions.tool_middleware import ToolCircuitBreaker
        except ImportError:
            return

        now = _time_module.monotonic()
        processed: dict[str, dict] = {}
        for tool_name, state_data in data.items():
            try:
                failures = int(state_data.get("failures", 0))
                # Fix 2: clamp failures to prevent instant OPEN on restore
                failures = min(failures, 1000)
                state_str = str(state_data.get("state", "CLOSED"))
                opened_at = float(state_data.get("opened_at", 0))
                # Fix 1: preserve _probe_in_flight from snapshot (default False)
                probe_in_flight = bool(state_data.get("_probe_in_flight", False))

                # Cooldown expiry: OPEN → HALF_OPEN if cooldown elapsed.
                # Guard against pre-reboot monotonic timestamps
                # (opened_at > now → negative elapsed → treat as expired).
                elapsed = now - opened_at
                if state_str == "OPEN":
                    if elapsed < 0 or elapsed >= ToolCircuitBreaker.COOLDOWN_SECONDS:
                        state_str = "HALF_OPEN"
                        logger.debug(
                            "CB %r: cooldown expired on restore (elapsed=%.1fs, "
                            "opened_at=%r, now=%r) → HALF_OPEN",
                            tool_name,
                            elapsed,
                            opened_at,
                            now,
                        )

                processed[tool_name] = {
                    "failures": failures,
                    "state": state_str,
                    "opened_at": opened_at,
                    "_probe_in_flight": probe_in_flight,
                }
            except Exception:
                logger.debug(
                    "Failed to process CB data for %s", tool_name, exc_info=True
                )

        await ToolCircuitBreaker.restore_state(processed)


# Singleton
snapshot_engine = SnapshotEngine()
