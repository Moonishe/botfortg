"""Circuit Breaker Telemetry — state transition tracking and reporting.

Tracks every state transition of :class:`ToolCircuitBreaker` in a ring buffer
per tool. Provides query methods for the :mod:`mcp_self_circuits` MCP tool
and for debugging / dashboards.

Usage::

    from src.core.observability.circuit_telemetry import circuit_telemetry

    await circuit_telemetry.record_transition(
        "mcp_search", "CLOSED", "OPEN", "failure_threshold",
    )
    report = await circuit_telemetry.get_report()
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_MAX_EVENTS_PER_TOOL: int = 50


@dataclass
class CBEvent:
    """A single circuit breaker state transition event."""

    tool_name: str
    from_state: str
    to_state: str
    timestamp: float
    reason: str  # failure_threshold | cooldown_expired | probe_success | probe_failure | manual_reset


class CircuitTelemetry:
    """Tracks circuit breaker state transitions and provides reports.

    Thread-safe via :class:`asyncio.Lock`.  Each tool keeps a ring buffer
    of up to ``_MAX_EVENTS_PER_TOOL`` transition events.

    *Zero-import design* (Issue 1 fix): ``ToolCircuitBreaker`` pushes state
    via :class:`EventBus` → ``CircuitTelemetry`` subscribes.  Neither module
    imports the other at class level.  ``_current_states`` is the pushed
    cache that query methods read internally.
    """

    def __init__(self) -> None:
        self._events: dict[str, deque[CBEvent]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._initialised: bool = False
        self._current_states: dict[
            str, dict
        ] = {}  # {tool_name: {failures, state, opened_at, ...}}
        self._current_states_lock: asyncio.Lock = asyncio.Lock()

    # ── Initialisation ────────────────────────────────────────────

    @classmethod
    async def init(cls) -> None:
        """Subscribe to EventBus so breaker pushes land in ``_current_states`` cache.

        Must be called once at startup (see :file:`src/main.py`) before the
        breaker emits its full-state snapshot.
        """
        if circuit_telemetry._initialised:
            return
        from src.core.events.event_bus import event_bus, CIRCUIT_STATE

        event_bus.subscribe(CIRCUIT_STATE, cls._on_circuit_state)
        circuit_telemetry._initialised = True
        logger.info("CircuitTelemetry subscribed to CIRCUIT_STATE events")

    @classmethod
    async def _on_circuit_state(cls, **kwargs) -> None:
        """Handler for ``CIRCUIT_STATE`` events emitted by ToolCircuitBreaker."""
        await circuit_telemetry._on_state_push(**kwargs)

    async def _on_state_push(
        self,
        *,
        tool_name: str,
        from_state: str,
        to_state: str,
        reason: str,
        failures: int = 0,
    ) -> CBEvent | None:
        """Update ``_current_states`` cache + record transition event.

        Returns the recorded CBEvent (or None if the event is a no-op duplicate).
        Used by both direct EventBus pushes and legacy record_transition calls.
        """
        import time as _time

        async with self._current_states_lock:
            self._current_states[tool_name] = {
                "failures": failures,
                "state": to_state,
                "opened_at": _time.monotonic() if to_state == "OPEN" else 0.0,
                "_probe_in_flight": to_state == "HALF_OPEN",
            }

        # Record transition event
        event = CBEvent(
            tool_name=tool_name,
            from_state=from_state,
            to_state=to_state,
            timestamp=_time.monotonic(),
            reason=reason,
        )
        async with self._lock:
            q = self._events.get(tool_name)
            if q is None:
                q = deque(maxlen=_MAX_EVENTS_PER_TOOL)
                self._events[tool_name] = q
            q.append(event)
        logger.debug(
            "CB transition: %r %s→%s (%s)", tool_name, from_state, to_state, reason
        )
        return event

    # ── Record ────────────────────────────────────────────────────

    async def record_transition(
        self,
        tool_name: str,
        from_state: str,
        to_state: str,
        reason: str,
    ) -> None:
        """Record a state transition event.

        Args:
            tool_name: Tool identifier.
            from_state: Previous circuit state (CLOSED/OPEN/HALF_OPEN).
            to_state: New circuit state.
            reason: Reason for the transition (see :class:`CBEvent`).
        """
        # Delegate to _on_state_push so _current_states cache stays consistent.
        # EventBus pushes already go through _on_state_push; this path handles
        # legacy direct calls (e.g., tests).
        await self._on_state_push(
            tool_name=tool_name,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            failures=0,
        )

    # ── Query ─────────────────────────────────────────────────────

    async def get_report(self) -> dict:
        """Return a summary report of all tracked circuits.

        Returns a dict with per-tool status (current state, transition count)
        plus aggregate stats.
        """
        # Use _current_states cache (pushed via EventBus) — zero imports.
        async with self._current_states_lock:
            all_states = dict(self._current_states)

        # Collect event data under self._lock
        async with self._lock:
            tools = set(all_states.keys()) | set(self._events.keys())
            event_counts: dict[str, int] = {}
            last_transitions: dict[str, dict | None] = {}
            for name in sorted(tools):
                events = self._events.get(name, deque())
                event_counts[name] = len(events)
                last_transitions[name] = (
                    {
                        "from_state": events[-1].from_state,
                        "to_state": events[-1].to_state,
                        "reason": events[-1].reason,
                    }
                    if events
                    else None
                )

        # Build report
        circuits: dict = {}
        for name in sorted(tools):
            cb_state = all_states.get(name, {})
            circuits[name] = {
                "current_state": cb_state.get("state", "CLOSED"),
                "failures": cb_state.get("failures", 0),
                "cooldown_remaining": await self._get_cooldown_remaining(name),
                "transition_count": event_counts[name],
                "last_transition": last_transitions[name],
            }

        return {
            "total_circuits": len(circuits),
            "circuits": circuits,
            "aggregate": await self.get_aggregate_stats(),
        }

    async def get_tool_status(self, tool_name: str) -> dict:
        """Return detailed status for a single tool's circuit.

        Includes current breaker state, recent history, and cooldown info.
        """
        async with self._current_states_lock:
            cb_state = self._current_states.get(tool_name, {})

        cooldown = await self._get_cooldown_remaining(tool_name)

        async with self._lock:
            events = self._events.get(tool_name, deque())
            history = [
                {
                    "from_state": e.from_state,
                    "to_state": e.to_state,
                    "timestamp": e.timestamp,
                    "reason": e.reason,
                }
                for e in events
            ]

        return {
            "tool_name": tool_name,
            "current_state": cb_state.get("state", "CLOSED"),
            "failures": cb_state.get("failures", 0),
            "cooldown_remaining": cooldown,
            "transition_count": len(history),
            "history": history,
        }

    async def _get_cooldown_remaining(self, tool_name: str) -> float:
        """Compute cooldown from ``_current_states`` cache (no ToolCircuitBreaker import)."""
        import time as _time

        async with self._current_states_lock:
            state = self._current_states.get(tool_name, {})
        if state.get("state") != "OPEN":
            return 0.0
        elapsed = _time.monotonic() - state.get("opened_at", 0.0)
        if elapsed < 0:  # stale (pre-reboot) timestamp
            return 0.0
        remaining = 120.0 - elapsed  # ponytail: hardcoded COOLDOWN_SECONDS=120
        return max(0.0, round(remaining, 3))

    async def get_history(self, tool_name: str, limit: int = 10) -> list[CBEvent]:
        """Return recent transition events for *tool_name*, most recent first.

        Args:
            tool_name: Tool identifier.
            limit: Maximum events to return (default 10).
        """
        async with self._lock:
            events = self._events.get(tool_name, deque())
            return list(events)[-limit:][::-1]

    async def get_all_tool_names(self) -> set[str]:
        """Return union of active CB states + event keys.

        Public method for ``mcp_self_circuits`` — avoids direct ``_events``/``_lock`` access.
        """
        async with self._current_states_lock:
            state_keys = set(self._current_states.keys())
        async with self._lock:
            return state_keys | set(self._events.keys())

    async def get_aggregate_stats(self) -> dict:
        """Return aggregate statistics across all tracked circuits.

        Includes counts of OPEN circuits, average recovery time, etc.
        """
        async with self._current_states_lock:
            all_states = dict(self._current_states)
        open_count = sum(1 for s in all_states.values() if s.get("state") == "OPEN")
        half_open_count = sum(
            1 for s in all_states.values() if s.get("state") == "HALF_OPEN"
        )

        # Compute average recovery time (OPEN→CLOSED duration) under lock
        recovery_times: list[float] = []
        total_transitions = 0
        async with self._lock:
            total_transitions = sum(len(events) for events in self._events.values())
            for _name, events in self._events.items():
                for i, event in enumerate(events):
                    if event.reason == "probe_success" and event.to_state == "CLOSED":
                        # Look backwards for the matching OPEN transition
                        for j in range(i - 1, -1, -1):
                            prev = events[j]
                            if prev.to_state == "OPEN":
                                recovery = event.timestamp - prev.timestamp
                                recovery_times.append(recovery)
                                break

        return {
            "total_tools_tracked": len(all_states),
            "open_circuits": open_count,
            "half_open_circuits": half_open_count,
            "avg_recovery_seconds": (
                round(sum(recovery_times) / len(recovery_times), 3)
                if recovery_times
                else 0.0
            ),
            "total_transitions": total_transitions,
        }

    async def cleanup_stale(self) -> int:
        """Remove empty event deques for tools no longer tracked.

        Deques with zero events serve no purpose — they accumulate when
        a tool name appears transiently (e.g., dynamically-generated names).
        Called periodically from the global cleanup loop.

        Returns:
            Number of entries removed.
        """
        async with self._lock:
            stale = [name for name, events in self._events.items() if not events]
            for name in stale:
                del self._events[name]
        if stale:
            logger.debug(
                "CircuitTelemetry cleanup_stale: removed %d empty entries (%d remain)",
                len(stale),
                len(self._events),
            )
        return len(stale)


# ── Module-level singleton ────────────────────────────────────────

circuit_telemetry = CircuitTelemetry()
