"""Tests for Circuit Breaker Telemetry — Phase 2.3."""

import asyncio
import time

import pytest

from src.core.observability.circuit_telemetry import (
    CBEvent,
    CircuitTelemetry,
    circuit_telemetry,
)


@pytest.fixture(autouse=True)
async def _reset_telemetry():
    """Reset circuit telemetry and breaker state before each test."""
    from src.core.actions.tool_middleware import ToolCircuitBreaker

    # Rebind locks to the current test's event loop BEFORE using them.
    # pytest-asyncio creates a fresh loop per test; module-level locks created
    # at import time are bound to a different loop and would raise RuntimeError
    # when acquired.
    circuit_telemetry._lock = asyncio.Lock()
    circuit_telemetry._current_states_lock = asyncio.Lock()
    ToolCircuitBreaker._lock = asyncio.Lock()

    await ToolCircuitBreaker.reset()
    # Clear telemetry events & pushed state cache
    circuit_telemetry._events.clear()
    circuit_telemetry._current_states.clear()
    # Ensure EventBus wiring (idempotent after first call)
    await circuit_telemetry.init()
    yield
    await ToolCircuitBreaker.reset()
    circuit_telemetry._events.clear()
    circuit_telemetry._current_states.clear()
    # Restore defaults
    ToolCircuitBreaker.FAILURE_THRESHOLD = 5
    ToolCircuitBreaker.COOLDOWN_SECONDS = 120.0


# ── CircuitTelemetry unit tests ──────────────────────────────────


class TestCircuitTelemetry:
    """Unit tests for CircuitTelemetry event tracking and queries."""

    async def test_record_transition_stores_events(self) -> None:
        """record_transition() stores events in the ring buffer."""
        ct = CircuitTelemetry()

        await ct.record_transition("test_tool", "CLOSED", "OPEN", "failure_threshold")

        async with ct._lock:
            assert "test_tool" in ct._events
            assert len(ct._events["test_tool"]) == 1
            event = ct._events["test_tool"][0]
            assert event.tool_name == "test_tool"
            assert event.from_state == "CLOSED"
            assert event.to_state == "OPEN"
            assert event.reason == "failure_threshold"
            assert isinstance(event.timestamp, float)

    async def test_ring_buffer_caps_at_max(self) -> None:
        """Events per tool are capped at 50 (ring buffer)."""
        ct = CircuitTelemetry()

        for i in range(60):
            await ct.record_transition(
                "busy_tool", "CLOSED", "OPEN", "failure_threshold"
            )

        async with ct._lock:
            assert len(ct._events["busy_tool"]) == 50
            # First 10 should be dropped
            assert ct._events["busy_tool"][0].tool_name == "busy_tool"

    async def test_get_history_returns_limited(self) -> None:
        """get_history() returns most recent events first, limited."""
        ct = CircuitTelemetry()
        for i in range(5):
            await ct.record_transition(
                f"tool_{i}", "CLOSED", "OPEN", "failure_threshold"
            )

        history = await ct.get_history("tool_0", limit=3)
        assert len(history) == 1  # Only 1 event for tool_0

    async def test_get_history_most_recent_first(self) -> None:
        """get_history() returns events newest first."""
        ct = CircuitTelemetry()
        await ct.record_transition("t", "CLOSED", "OPEN", "failure_threshold")
        await asyncio.sleep(0.01)
        await ct.record_transition("t", "OPEN", "HALF_OPEN", "cooldown_expired")
        await asyncio.sleep(0.01)
        await ct.record_transition("t", "HALF_OPEN", "CLOSED", "probe_success")

        history = await ct.get_history("t", limit=10)
        assert len(history) == 3
        assert history[0].reason == "probe_success"
        assert history[1].reason == "cooldown_expired"
        assert history[2].reason == "failure_threshold"

    async def test_history_empty_tool_returns_empty(self) -> None:
        """get_history() for unknown tool returns empty list."""
        ct = CircuitTelemetry()
        history = await ct.get_history("unknown", limit=5)
        assert history == []

    async def test_get_report_returns_structure(self) -> None:
        """get_report() returns summary with circuits and aggregate."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        # Create some circuit state
        for _ in range(5):
            await ToolCircuitBreaker.record_failure("report_tool")

        report = await circuit_telemetry.get_report()
        assert "total_circuits" in report
        assert "circuits" in report
        assert "aggregate" in report
        assert "report_tool" in report["circuits"]
        assert report["circuits"]["report_tool"]["current_state"] == "OPEN"

    async def test_get_tool_status_returns_details(self) -> None:
        """get_tool_status() returns detailed status for a tool."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        for _ in range(5):
            await ToolCircuitBreaker.record_failure("status_tool")

        status = await circuit_telemetry.get_tool_status("status_tool")
        assert status["tool_name"] == "status_tool"
        assert status["current_state"] == "OPEN"
        assert status["failures"] == 5
        assert status["transition_count"] >= 1
        assert "cooldown_remaining" in status
        assert "history" in status

    async def test_get_tool_status_unknown_tool(self) -> None:
        """get_tool_status() for unknown tool returns CLOSED defaults."""
        status = await circuit_telemetry.get_tool_status("no_such_tool")
        assert status["current_state"] == "CLOSED"
        assert status["failures"] == 0

    async def test_get_aggregate_stats(self) -> None:
        """get_aggregate_stats() returns aggregate metrics."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        # Trip two circuits
        for _ in range(5):
            await ToolCircuitBreaker.record_failure("agg_tool_a")
        # Only 3 failures on tool B — not tripped
        for _ in range(3):
            await ToolCircuitBreaker.record_failure("agg_tool_b")

        stats = await circuit_telemetry.get_aggregate_stats()
        assert "total_tools_tracked" in stats
        assert "open_circuits" in stats
        assert stats["open_circuits"] == 1  # Only agg_tool_a is OPEN
        assert stats["total_transitions"] >= 1


# ── ToolCircuitBreaker query method tests ────────────────────────


class TestCircuitBreakerQueries:
    """Tests for new query methods on ToolCircuitBreaker."""

    async def test_get_state_returns_copy(self) -> None:
        """get_state() returns a CBState copy."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        for _ in range(3):
            await ToolCircuitBreaker.record_failure("query_tool")

        state = await ToolCircuitBreaker.get_state("query_tool")
        assert state is not None
        assert state.failures == 3
        assert state.state == "CLOSED"

        # Mutating the copy should not affect internal state
        state.failures = 999
        internal = await ToolCircuitBreaker.get_state("query_tool")
        assert internal is not None
        assert internal.failures == 3

    async def test_get_state_unknown_returns_none(self) -> None:
        """get_state() returns None for unknown tool."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        state = await ToolCircuitBreaker.get_state("never_seen")
        assert state is None

    async def test_get_all_states(self) -> None:
        """get_all_states() returns all circuit states."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        await ToolCircuitBreaker.record_failure("tool_a")
        await ToolCircuitBreaker.record_failure("tool_b")

        all_states = await ToolCircuitBreaker.get_all_states()
        assert "tool_a" in all_states
        assert "tool_b" in all_states
        assert all_states["tool_a"].failures == 1

    async def test_get_cooldown_remaining_open(self) -> None:
        """get_cooldown_remaining() returns positive for OPEN circuit."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        for _ in range(5):
            await ToolCircuitBreaker.record_failure("cool_tool")

        remaining = await ToolCircuitBreaker.get_cooldown_remaining("cool_tool")
        assert remaining > 0
        assert remaining <= ToolCircuitBreaker.COOLDOWN_SECONDS

    async def test_get_cooldown_remaining_closed(self) -> None:
        """get_cooldown_remaining() returns 0 for CLOSED circuit."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        remaining = await ToolCircuitBreaker.get_cooldown_remaining("closed_tool")
        assert remaining == 0.0

        await ToolCircuitBreaker.record_failure("closed_tool")
        remaining = await ToolCircuitBreaker.get_cooldown_remaining("closed_tool")
        assert remaining == 0.0  # Still CLOSED, not OPEN

    async def test_get_cooldown_remaining_half_open(self) -> None:
        """get_cooldown_remaining() returns 0 for HALF_OPEN circuit."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        ToolCircuitBreaker.COOLDOWN_SECONDS = 0.05
        try:
            for _ in range(5):
                await ToolCircuitBreaker.record_failure("ho_tool")
            time.sleep(0.06)
            await ToolCircuitBreaker.check("ho_tool")  # Triggers OPEN→HALF_OPEN

            remaining = await ToolCircuitBreaker.get_cooldown_remaining("ho_tool")
            assert remaining == 0.0
        finally:
            ToolCircuitBreaker.COOLDOWN_SECONDS = 120.0


# ── Transition detection tests ───────────────────────────────────


class TestTransitionDetection:
    """Verify that circuit state transitions emit telemetry events."""

    async def test_closed_to_open_emits_event(self) -> None:
        """5 failures from CLOSED → OPEN must emit a transition event."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        for _ in range(5):
            await ToolCircuitBreaker.record_failure("to_open")

        history = await circuit_telemetry.get_history("to_open", limit=5)
        # At least one OPEN transition
        open_events = [e for e in history if e.to_state == "OPEN"]
        assert len(open_events) >= 1
        assert open_events[0].from_state == "CLOSED"
        assert open_events[0].reason == "failure_threshold"

    async def test_open_to_half_open_emits_event(self) -> None:
        """Cooldown expiry (OPEN → HALF_OPEN) must emit a transition event."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        ToolCircuitBreaker.COOLDOWN_SECONDS = 0.05
        try:
            for _ in range(5):
                await ToolCircuitBreaker.record_failure("ho_trans")

            time.sleep(0.06)
            await ToolCircuitBreaker.check("ho_trans")  # Trigger transition

            history = await circuit_telemetry.get_history("ho_trans", limit=5)
            ho_events = [e for e in history if e.to_state == "HALF_OPEN"]
            assert len(ho_events) >= 1
            assert ho_events[0].from_state == "OPEN"
            assert ho_events[0].reason == "cooldown_expired"
        finally:
            ToolCircuitBreaker.COOLDOWN_SECONDS = 120.0

    async def test_half_open_to_closed_emits_event(self) -> None:
        """Successful probe (HALF_OPEN → CLOSED) must emit event."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        ToolCircuitBreaker.COOLDOWN_SECONDS = 0.05
        try:
            for _ in range(5):
                await ToolCircuitBreaker.record_failure("probe_ok")

            time.sleep(0.06)
            await ToolCircuitBreaker.check("probe_ok")  # → HALF_OPEN
            await ToolCircuitBreaker.record_success("probe_ok")  # → CLOSED

            history = await circuit_telemetry.get_history("probe_ok", limit=5)
            closed_events = [
                e
                for e in history
                if e.to_state == "CLOSED" and e.reason == "probe_success"
            ]
            assert len(closed_events) >= 1
        finally:
            ToolCircuitBreaker.COOLDOWN_SECONDS = 120.0

    async def test_half_open_to_open_emits_event(self) -> None:
        """Failed probe (HALF_OPEN → OPEN) must emit event."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        ToolCircuitBreaker.COOLDOWN_SECONDS = 0.05
        try:
            for _ in range(5):
                await ToolCircuitBreaker.record_failure("probe_fail")

            time.sleep(0.06)
            await ToolCircuitBreaker.check("probe_fail")  # → HALF_OPEN
            await ToolCircuitBreaker.record_failure("probe_fail")  # → OPEN

            history = await circuit_telemetry.get_history("probe_fail", limit=5)
            probe_fail_events = [
                e
                for e in history
                if e.reason == "probe_failure" and e.to_state == "OPEN"
            ]
            assert len(probe_fail_events) >= 1
        finally:
            ToolCircuitBreaker.COOLDOWN_SECONDS = 120.0

    async def test_reset_emits_manual_reset_event(self) -> None:
        """Manual reset emits transition with reason='manual_reset'."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        for _ in range(5):
            await ToolCircuitBreaker.record_failure("reset_me")

        await ToolCircuitBreaker.reset("reset_me")

        history = await circuit_telemetry.get_history("reset_me", limit=5)
        reset_events = [e for e in history if e.reason == "manual_reset"]
        assert len(reset_events) >= 1
        assert reset_events[0].to_state == "CLOSED"


# ── MCP tool tests ───────────────────────────────────────────────


class TestMCPSelfCircuits:
    """Integration tests for mcp_self_circuits MCP tool."""

    async def test_action_status(self) -> None:
        """mcp_self_circuits action='status' returns circuit report."""
        from src.core.actions.mcp_self_circuits import mcp_self_circuits
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        # Create some state
        for _ in range(5):
            await ToolCircuitBreaker.record_failure("mcp_tool")

        result = await mcp_self_circuits(action="status")
        assert "total_circuits" in result
        assert "circuits" in result
        assert "aggregate" in result
        assert result["circuits"]["mcp_tool"]["current_state"] == "OPEN"

    async def test_action_tool(self) -> None:
        """mcp_self_circuits action='tool' returns specific tool status."""
        from src.core.actions.mcp_self_circuits import mcp_self_circuits
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        await ToolCircuitBreaker.record_failure("specific_tool")

        result = await mcp_self_circuits(action="tool", tool_name="specific_tool")
        assert result["tool_name"] == "specific_tool"
        assert result["current_state"] == "CLOSED"
        assert result["failures"] == 1

    async def test_action_tool_missing_name(self) -> None:
        """action='tool' without tool_name returns error."""
        from src.core.actions.mcp_self_circuits import mcp_self_circuits

        result = await mcp_self_circuits(action="tool")
        assert "error" in result

    async def test_action_history(self) -> None:
        """action='history' returns recent transitions."""
        from src.core.actions.mcp_self_circuits import mcp_self_circuits
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        for _ in range(5):
            await ToolCircuitBreaker.record_failure("hist_tool")

        result = await mcp_self_circuits(action="history")
        assert "total_entries" in result
        assert "history" in result
        assert result["total_entries"] >= 1

    async def test_action_reset(self) -> None:
        """action='reset' clears circuit for a tool."""
        from src.core.actions.mcp_self_circuits import mcp_self_circuits
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        for _ in range(5):
            await ToolCircuitBreaker.record_failure("to_reset")
        assert await ToolCircuitBreaker.get_state("to_reset") is not None

        result = await mcp_self_circuits(
            action="reset", tool_name="to_reset", _confirmed=True
        )
        assert result["reset"] is True
        assert await ToolCircuitBreaker.get_state("to_reset") is None

    async def test_action_reset_missing_name(self) -> None:
        """action='reset' without tool_name returns error."""
        from src.core.actions.mcp_self_circuits import mcp_self_circuits

        result = await mcp_self_circuits(action="reset")
        assert "error" in result

    async def test_action_unknown(self) -> None:
        """Unknown action returns error."""
        from src.core.actions.mcp_self_circuits import mcp_self_circuits

        result = await mcp_self_circuits(action="foobar")
        assert "error" in result
        assert "Unknown action" in result["error"]


# ── Smoke: import and decorator registration ─────────────────────


class TestSmoke:
    """Verify module loads and tool is registered."""

    def test_circuit_telemetry_module_imports(self) -> None:
        """Verify circuit_telemetry module imports without error."""
        from src.core.observability.circuit_telemetry import (
            CircuitTelemetry,
            CBEvent,
            circuit_telemetry,
        )

        assert CircuitTelemetry is not None
        assert CBEvent is not None
        assert circuit_telemetry is not None

    def test_mcp_self_circuits_is_decorated_tool(self) -> None:
        """Verify mcp_self_circuits is registered as a tool."""
        from src.core.actions.tool_registry import tool_registry

        spec = tool_registry.get("mcp_self_circuits")
        assert spec is not None, "mcp_self_circuits not found in tool_registry"
        assert spec.name == "mcp_self_circuits"
        assert spec.category == "admin"
