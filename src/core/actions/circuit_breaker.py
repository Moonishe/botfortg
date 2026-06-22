"""Per-tool circuit breaker — blocks calls after consecutive failures.

States:
    CLOSED     — normal operation, counting failures
    OPEN       — >FAILURE_THRESHOLD consecutive failures, blocked for COOLDOWN
    HALF_OPEN  — cooldown expired, a single probe call is permitted

If the probe succeeds → back to CLOSED (failures=0).
If the probe fails    → back to OPEN (cooldown restarts).

Originally part of ``tool_middleware.py`` — extracted for module hygiene.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from src.core.events.event_bus import CIRCUIT_STATE, event_bus

logger = logging.getLogger(__name__)


@dataclass
class CBState:
    """Internal state of a single tool's circuit breaker."""

    failures: int = 0
    state: str = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
    opened_at: float = 0.0
    _probe_in_flight: bool = (
        False  # ponytail: simple bool, no semaphore; prevents multi-probe race
    )


class ToolCircuitBreaker:
    """Per-tool circuit breaker that blocks calls after consecutive failures.

    States:
        CLOSED     — normal operation, counting failures
        OPEN       — >FAILURE_THRESHOLD consecutive failures, blocked for COOLDOWN
        HALF_OPEN  — cooldown expired, a single probe call is permitted

    If the probe succeeds → back to CLOSED (failures=0).
    If the probe fails    → back to OPEN (cooldown restarts).

    Usage (via middleware factory)::

        pre, post = _build_circuit_breaker()
        chain.register(name="circuit_breaker", priority=30, pre=pre, post=post)
    """

    _states: dict[str, CBState] = {}
    _lock: asyncio.Lock = asyncio.Lock()
    FAILURE_THRESHOLD: int = 5
    COOLDOWN_SECONDS: float = 120.0

    @classmethod
    async def check(cls, tool_name: str) -> bool:
        """Check whether *tool_name* is allowed to execute.

        Returns:
            ``True`` if the call is permitted, ``False`` if blocked (OPEN).
        """
        pending_emit: list[dict[str, Any]] = []
        result = True
        async with cls._lock:
            state = cls._states.get(tool_name)
            if state is None:
                return True  # No failures recorded — circuit is implicitly CLOSED

            if state.state == "CLOSED":
                return True
            if state.state == "HALF_OPEN":
                # Only allow one probe call at a time
                if state._probe_in_flight:
                    return False
                state._probe_in_flight = True
                return True  # Allow probe call

            # state == OPEN
            elapsed = time.monotonic() - state.opened_at
            # Guard against pre-reboot monotonic timestamps: negative elapsed
            # means ``opened_at`` is from a previous process — treat as expired.
            if elapsed < 0 or elapsed >= cls.COOLDOWN_SECONDS:
                old_state = state.state
                state.state = "HALF_OPEN"
                state._probe_in_flight = True  # this transition IS the probe flight
                # Defer emit until after lock release (P1-B3 fix)
                pending_emit.append(
                    {
                        "from_state": old_state,
                        "to_state": "HALF_OPEN",
                        "reason": "cooldown_expired",
                        "failures": state.failures,
                    }
                )
                logger.debug(
                    "Circuit for %r: OPEN → HALF_OPEN (%.1fs elapsed)",
                    tool_name,
                    elapsed,
                )
                result = True  # Allow probe call
            else:
                result = False  # Still in cooldown — block

        # Emit outside lock — prevents contention if handlers do I/O
        for ev in pending_emit:
            try:
                await event_bus.emit(CIRCUIT_STATE, tool_name=tool_name, **ev)
            except Exception:
                logger.debug(
                    "Failed to emit circuit state event for %r",
                    tool_name,
                    exc_info=True,
                )
        return result

    @classmethod
    async def record_success(cls, tool_name: str) -> None:
        """Record a successful call — reset failures (CLOSED)."""
        pending_emit: list[dict[str, Any]] = []
        async with cls._lock:
            state = cls._states.get(tool_name)
            if state is None:
                return  # No state — nothing to update
            old_state = state.state
            state.failures = 0
            state.state = "CLOSED"
            state._probe_in_flight = False
            if old_state != "CLOSED":
                pending_emit.append(
                    {
                        "from_state": old_state,
                        "to_state": "CLOSED",
                        "reason": "probe_success",
                        "failures": 0,
                    }
                )
                logger.info(
                    "Circuit for %r: %s → CLOSED (successful probe)",
                    tool_name,
                    old_state,
                )
        # Emit outside lock — prevents contention if handlers do I/O
        for ev in pending_emit:
            try:
                await event_bus.emit(CIRCUIT_STATE, tool_name=tool_name, **ev)
            except Exception:
                logger.debug(
                    "Failed to emit circuit state event for %r",
                    tool_name,
                    exc_info=True,
                )

    @classmethod
    async def record_failure(cls, tool_name: str) -> None:
        """Record a failed call and trip the circuit if threshold exceeded."""
        pending_emit: list[dict[str, Any]] = []
        async with cls._lock:
            state = cls._states.get(tool_name)
            if state is None:
                state = CBState()
                cls._states[tool_name] = state

            state.failures += 1
            state._probe_in_flight = False  # probe resolved (either success or failure)

            if state.state == "HALF_OPEN":
                # Probe failed — back to OPEN
                old_state = state.state
                state.state = "OPEN"
                state.opened_at = time.monotonic()
                pending_emit.append(
                    {
                        "from_state": old_state,
                        "to_state": "OPEN",
                        "reason": "probe_failure",
                        "failures": state.failures,
                    }
                )
                logger.warning(
                    "Circuit for %r: HALF_OPEN probe failed — back to OPEN "
                    "(failures=%d)",
                    tool_name,
                    state.failures,
                )
            elif state.failures >= cls.FAILURE_THRESHOLD and state.state != "OPEN":
                old_state = state.state
                state.state = "OPEN"
                state.opened_at = time.monotonic()
                pending_emit.append(
                    {
                        "from_state": old_state,
                        "to_state": "OPEN",
                        "reason": "failure_threshold",
                        "failures": state.failures,
                    }
                )
                logger.warning(
                    "Circuit for %r: tripped OPEN after %d failures",
                    tool_name,
                    state.failures,
                )
            else:
                # No transition yet, but push updated failure count to telemetry
                pending_emit.append(
                    {
                        "from_state": state.state,
                        "to_state": state.state,  # no transition
                        "reason": "failure_count",
                        "failures": state.failures,
                    }
                )
        # Emit outside lock — prevents contention if handlers do I/O
        for ev in pending_emit:
            try:
                await event_bus.emit(CIRCUIT_STATE, tool_name=tool_name, **ev)
            except Exception:
                logger.debug(
                    "Failed to emit circuit state event for %r",
                    tool_name,
                    exc_info=True,
                )

    @classmethod
    async def reset(cls, tool_name: str | None = None) -> None:
        """Reset circuit state for *tool_name* (or all tools if None/empty).

        Useful for testing.
        """
        pending_emit: list[dict[str, Any]] = []
        async with cls._lock:
            if not tool_name:
                for name in list(cls._states.keys()):
                    old_state = cls._states[name].state
                    cls._states[name]._probe_in_flight = False
                    pending_emit.append(
                        {
                            "tool_name": name,
                            "from_state": old_state,
                            "to_state": "CLOSED",
                            "reason": "manual_reset",
                            "failures": 0,
                        }
                    )
                cls._states.clear()
            else:
                old = cls._states.pop(tool_name, None)
                if old is not None:
                    old._probe_in_flight = (
                        False  # ponytail: reset clears the probe flight
                    )
                    pending_emit.append(
                        {
                            "tool_name": tool_name,
                            "from_state": old.state,
                            "to_state": "CLOSED",
                            "reason": "manual_reset",
                            "failures": 0,
                        }
                    )
        # Emit outside lock — prevents contention if handlers do I/O
        for ev in pending_emit:
            tn = ev.pop("tool_name")
            try:
                await event_bus.emit(CIRCUIT_STATE, tool_name=tn, **ev)
            except Exception:
                logger.debug(
                    "Failed to emit circuit state event for %r", tn, exc_info=True
                )

    @classmethod
    async def get_state(cls, tool_name: str) -> CBState | None:
        """Return a copy of the circuit state for *tool_name*, or ``None``."""
        async with cls._lock:
            state = cls._states.get(tool_name)
            if state is None:
                return None
            return CBState(
                failures=state.failures,
                state=state.state,
                opened_at=state.opened_at,
                _probe_in_flight=state._probe_in_flight,
            )

    @classmethod
    async def get_all_states(cls) -> dict[str, CBState]:
        """Return copies of all circuit states."""
        async with cls._lock:
            return {
                name: CBState(
                    failures=s.failures,
                    state=s.state,
                    opened_at=s.opened_at,
                    _probe_in_flight=s._probe_in_flight,
                )
                for name, s in cls._states.items()
            }

    @classmethod
    async def _push_full_state(cls) -> None:
        """Emit a CIRCUIT_STATE snapshot for every currently tracked tool.

        Called once at startup so that CircuitTelemetry can initialise its
        ``_current_states`` cache without importing ToolCircuitBreaker.
        """
        pending_emit: list[dict[str, Any]] = []
        async with cls._lock:
            for tool_name, state in cls._states.items():
                pending_emit.append(
                    {
                        "tool_name": tool_name,
                        "from_state": state.state,
                        "to_state": state.state,  # snapshot — no transition
                        "reason": "startup_snapshot",
                        "failures": state.failures,
                    }
                )
        # Emit outside lock — prevents contention if handlers do I/O
        for ev in pending_emit:
            tn = ev.pop("tool_name")
            try:
                await event_bus.emit(CIRCUIT_STATE, tool_name=tn, **ev)
            except Exception:
                logger.debug(
                    "Failed to emit circuit state event for %r", tn, exc_info=True
                )

    @classmethod
    async def capture_state(cls) -> dict[str, dict]:
        """Return a serializable snapshot of all circuit breaker states.

        Public method for snapshot engine — avoids direct ``_states``/``_lock`` access.
        """
        async with cls._lock:
            return {
                tool_name: {
                    "failures": state.failures,
                    "state": state.state,
                    "opened_at": state.opened_at,
                    "_probe_in_flight": state._probe_in_flight,
                }
                for tool_name, state in cls._states.items()
            }

    @classmethod
    async def restore_state(cls, data: dict) -> None:
        """Restore circuit breaker states from a snapshot dict.

        Public method for snapshot engine — avoids direct ``_states``/``_lock`` access.
        Accepts pre-processed data (caller handles cooldown expiry, clamping, etc.).
        """
        async with cls._lock:
            for tool_name, state_data in data.items():
                cls._states[tool_name] = CBState(
                    failures=int(state_data.get("failures", 0)),
                    state=str(state_data.get("state", "CLOSED")),
                    opened_at=float(state_data.get("opened_at", 0.0)),
                    _probe_in_flight=bool(state_data.get("_probe_in_flight", False)),
                )

    @classmethod
    async def get_cooldown_remaining(cls, tool_name: str) -> float:
        """Return seconds remaining for OPEN circuits, 0 for CLOSED/HALF_OPEN.

        Returns 0.0 if the circuit is not OPEN, expired, or ``opened_at``
        is stale (pre-reboot monotonic timestamp).
        """
        async with cls._lock:
            state = cls._states.get(tool_name)
            if state is None or state.state != "OPEN":
                return 0.0
            elapsed = time.monotonic() - state.opened_at
            # Negative elapsed → stale timestamp → treat as expired
            if elapsed < 0:
                return 0.0
            remaining = cls.COOLDOWN_SECONDS - elapsed
            return max(0.0, round(remaining, 3))

    @classmethod
    async def cleanup_stale(cls) -> int:
        """Remove CLOSED entries with zero failures (default state).

        These entries are re-created on next failure — keeping them is
        harmless but grows the dict unboundedly when tools are renamed
        or removed.  Called periodically from the global cleanup loop.

        Returns:
            Number of entries removed.
        """
        async with cls._lock:
            stale = [
                name
                for name, s in cls._states.items()
                if s.state == "CLOSED" and s.failures == 0
            ]
            for name in stale:
                del cls._states[name]
        if stale:
            logger.debug(
                "ToolCircuitBreaker cleanup_stale: removed %d idle entries (%d remain)",
                len(stale),
                len(cls._states),
            )
        return len(stale)
