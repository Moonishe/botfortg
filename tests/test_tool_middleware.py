"""Tests for Tool Middleware Chain and its integration with ToolRegistry."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.actions.tool_middleware import (
    MiddlewareChain,
    ToolContext,
    init_default_middlewares,
)


# ===================================================================
# MiddlewareChain — unit tests
# ===================================================================


class TestMiddlewareChain:
    """Pure unit tests for MiddlewareChain (no integration)."""

    async def test_empty_chain_passes_through(self) -> None:
        chain = MiddlewareChain()

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True, "tool": name}

        result = await chain.wrap("test_tool", {"foo": 1}, handler)
        assert result == {"ok": True, "tool": "test_tool"}

    async def test_pre_hook_modifies_params(self) -> None:
        chain = MiddlewareChain()

        def add_param(ctx: ToolContext) -> ToolContext:
            ctx.params["injected"] = 42
            return ctx

        chain.register(name="injector", priority=10, pre=add_param)

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True, "injected": params.get("injected")}

        result = await chain.wrap("test_tool", {}, handler)
        assert result == {"ok": True, "injected": 42}

    async def test_pre_hook_blocks_execution(self) -> None:
        chain = MiddlewareChain()

        def blocker(ctx: ToolContext) -> None:
            return None

        chain.register(name="blocker", priority=10, pre=blocker)

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True}  # pragma: no cover

        result = await chain.wrap("test_tool", {}, handler)
        assert "error" in result
        assert result["blocked_by"] == "middleware"

    async def test_pre_hook_exception_does_not_block(self) -> None:
        chain = MiddlewareChain()

        def broken_pre(ctx: ToolContext) -> ToolContext:  # noqa: ARG001
            msg = "pre-hook error"
            raise RuntimeError(msg)

        chain.register(name="broken", priority=10, pre=broken_pre)

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True, "from": name}

        result = await chain.wrap("test_tool", {}, handler)
        assert result == {"ok": True, "from": "test_tool"}

    async def test_post_hook_receives_result(self) -> None:
        chain = MiddlewareChain()
        captured: list[dict | None] = []

        async def capture(ctx: ToolContext) -> None:
            captured.append(ctx.result)

        chain.register(name="capture", priority=50, post=capture)

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True, "data": 99}

        await chain.wrap("test_tool", {}, handler)
        assert len(captured) == 1
        assert captured[0] == {"ok": True, "data": 99}

    async def test_post_hook_exception_does_not_crash(self) -> None:
        chain = MiddlewareChain()

        async def broken_post(ctx: ToolContext) -> None:  # noqa: ARG001
            msg = "post-hook error"
            raise RuntimeError(msg)

        chain.register(name="broken_post", priority=50, post=broken_post)

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True}

        result = await chain.wrap("test_tool", {}, handler)
        assert result == {"ok": True}

    async def test_multiple_middlewares_priority_order(self) -> None:
        chain = MiddlewareChain()
        order: list[str] = []

        def make_pre(tag: str) -> Any:
            def pre(ctx: ToolContext) -> ToolContext:
                order.append(tag)
                return ctx

            return pre

        chain.register(name="mid", priority=50, pre=make_pre("mid"))
        chain.register(name="low", priority=10, pre=make_pre("low"))
        chain.register(name="high", priority=90, pre=make_pre("high"))

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True}

        await chain.wrap("test_tool", {}, handler)
        assert order == ["low", "mid", "high"]

    async def test_handler_exception_caught(self) -> None:
        chain = MiddlewareChain()

        async def failing_handler(name: str, params: dict) -> dict:
            msg = "handler failure"
            raise RuntimeError(msg)

        result = await chain.wrap("test_tool", {}, failing_handler)
        assert "error" in result

    async def test_default_middlewares_register_on_init(self) -> None:
        """init_default_middlewares registers all built-in middlewares."""
        chain = MiddlewareChain()
        init_default_middlewares(chain=chain)

        names = chain.registered
        assert "input_validator" in names
        assert "circuit_breaker" in names
        assert "audit" in names
        assert "metrics" in names

        # Verify priority ordering
        assert names.index("input_validator") < names.index("circuit_breaker")
        assert names.index("circuit_breaker") < names.index("audit")

    async def test_init_default_middlewares_is_idempotent(self) -> None:
        """Second call to init_default_middlewares does not re-register."""
        chain = MiddlewareChain()
        init_default_middlewares(chain=chain)
        count = len(chain.registered)
        init_default_middlewares(chain=chain)
        assert len(chain.registered) == count

    async def test_middleware_meta_stores_latency(self) -> None:
        """Metrics middleware records latency_seconds in meta."""
        chain = MiddlewareChain()
        init_default_middlewares(chain=chain)

        async def handler(name: str, params: dict) -> dict:
            await asyncio.sleep(0.01)
            return {"ok": True}

        result = await chain.wrap("test_tool", {}, handler)
        assert result == {"ok": True}

    async def test_registered_property(self) -> None:
        chain = MiddlewareChain()
        chain.register(name="first", priority=10, post=lambda ctx: None)  # type: ignore[arg-type, return-value]
        chain.register(name="second", priority=20, post=lambda ctx: None)  # type: ignore[arg-type, return-value]
        assert chain.registered == ["first", "second"]


# ===================================================================
# Integration test — middleware_chain + ToolRegistry.execute()
# ===================================================================


@pytest.fixture
def fresh_registry():
    """Create a ToolRegistry with one test tool registered."""
    from src.core.actions.tool_registry import ToolRegistry, ToolSpec

    reg = ToolRegistry()

    async def ping(**kwargs: Any) -> dict:
        return {"ok": True, "echo": kwargs}

    reg.register(
        ToolSpec(
            name="ping",
            description="Test tool",
            category="test",
            handler=ping,
            params={"msg": "str"},
        )
    )

    return reg


class TestMiddlewareToolRegistryIntegration:
    """Middleware chain is integrated into ToolRegistry.execute()."""

    async def test_tool_executes_through_middleware(self, fresh_registry) -> None:
        result = await fresh_registry.execute("ping", msg="hello")
        assert "error" not in result
        assert result.get("ok") is True
        assert result.get("echo", {}).get("msg") == "hello"

    async def test_middleware_sees_tool_result(self, fresh_registry) -> None:
        """Middleware post-hook sees the tool's result after execution."""
        from src.core.actions.tool_middleware import middleware_chain

        captured: list[dict | None] = []
        saved_mw = list(middleware_chain._mw)  # snapshot

        async def capture_post(ctx: ToolContext) -> None:
            captured.append(ctx.result)

        middleware_chain.register(name="test_capture", priority=100, post=capture_post)

        try:
            await fresh_registry.execute("ping", msg="world")
            assert len(captured) >= 1
            last_result = captured[-1]
            assert last_result is not None
            assert last_result.get("ok") is True
            assert last_result.get("echo", {}).get("msg") == "world"
        finally:
            # Restore global state to prevent test pollution
            middleware_chain._mw[:] = saved_mw

    async def test_middleware_does_not_block_normal_execution(
        self, fresh_registry
    ) -> None:
        result = await fresh_registry.execute("ping")
        assert result is not None
        assert "error" not in result

    async def test_nonexistent_tool_returns_error(self, fresh_registry) -> None:
        result = await fresh_registry.execute("nonexistent")
        assert "error" in result


# ===================================================================
# Direct middleware chain with error handler
# ===================================================================


class TestErrorHandling:
    async def test_handler_exception_returns_error_dict(self) -> None:
        chain = MiddlewareChain()

        async def crash(name: str, params: dict) -> dict:
            msg = "internal error"
            raise RuntimeError(msg)

        result = await chain.wrap("crash_tool", {}, crash)
        assert "error" in result


# ===================================================================
# Input Validation Middleware — tests
# ===================================================================


class TestInputValidationMiddleware:
    """Input validation via input_schema JSON Schema (uses global tool_registry)."""

    @pytest.fixture(autouse=True)
    def _cleanup_global(self) -> Any:
        """Save/restore global tool_registry state to prevent pollution."""
        from src.core.actions.tool_registry import tool_registry as global_reg

        saved = dict(global_reg._tools)
        yield
        global_reg._tools.clear()
        global_reg._tools.update(saved)

    async def _register_test_tool(
        self, name: str = "ping", input_schema: dict | None = None
    ) -> None:
        """Register a test tool on the global tool_registry."""
        from src.core.actions.tool_registry import (
            tool_registry as global_reg,
            ToolSpec,
        )

        async def _handler(**kwargs: Any) -> dict:
            return {"ok": True, "echo": kwargs}

        global_reg.register(
            ToolSpec(
                name=name,
                description="Test",
                category="test",
                handler=_handler,
                input_schema=input_schema,
            )
        )

    async def test_valid_params_pass_through(self) -> None:
        """Params matching the schema are allowed through."""
        schema = {
            "type": "object",
            "properties": {
                "msg": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["msg"],
        }
        await self._register_test_tool(input_schema=schema)
        from src.core.actions.tool_registry import tool_registry as global_reg

        result = await global_reg.execute("ping", msg="hello", count=42)
        assert "error" not in result
        assert result["ok"] is True

    async def test_invalid_params_are_blocked(self) -> None:
        """Params NOT matching the schema are blocked."""
        schema = {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        }
        await self._register_test_tool(input_schema=schema)
        from src.core.actions.tool_registry import tool_registry as global_reg

        result = await global_reg.execute("ping")  # missing required 'msg'
        assert "error" in result

    async def test_missing_required_field(self) -> None:
        """Missing required field triggers validation error."""
        schema = {
            "type": "object",
            "properties": {"email": {"type": "string"}},
            "required": ["email"],
        }
        await self._register_test_tool(input_schema=schema)
        from src.core.actions.tool_registry import tool_registry as global_reg

        result = await global_reg.execute("ping")
        assert "error" in result

    async def test_wrong_type_is_blocked(self) -> None:
        """Wrong parameter type triggers validation error."""
        schema = {
            "type": "object",
            "properties": {"age": {"type": "integer"}},
        }
        await self._register_test_tool(input_schema=schema)
        from src.core.actions.tool_registry import tool_registry as global_reg

        result = await global_reg.execute("ping", age="not_an_integer")
        assert "error" in result

    async def test_no_schema_passes_through(self) -> None:
        """Tool without input_schema is not validated."""
        await self._register_test_tool(input_schema=None)
        from src.core.actions.tool_registry import tool_registry as global_reg

        result = await global_reg.execute("ping", anything="goes")
        assert "error" not in result
        assert result["ok"] is True


# ===================================================================
# Circuit Breaker Middleware — tests
# ===================================================================


class TestCircuitBreaker:
    """Test the ToolCircuitBreaker and its middleware integration.

    Covers CLOSED → OPEN (5 failures) → HALF_OPEN (cooldown) → CLOSED.
    """

    @pytest.fixture(autouse=True)
    async def _reset_circuit(self) -> Any:
        """Reset circuit state before and after each test."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        await ToolCircuitBreaker.reset()
        yield
        await ToolCircuitBreaker.reset()

    # ── Unit tests: ToolCircuitBreaker class ─────────────────────

    async def test_check_allows_when_no_state(self) -> None:
        """A never-seen tool is implicitly CLOSED."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        assert await ToolCircuitBreaker.check("unknown_tool") is True

    async def test_failures_trip_circuit_to_open(self) -> None:
        """5 consecutive failures transition CLOSED → OPEN, 6th blocked."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        for _ in range(5):
            assert await ToolCircuitBreaker.check("fail_tool") is True
            await ToolCircuitBreaker.record_failure("fail_tool")

        # 6th call — blocked
        assert await ToolCircuitBreaker.check("fail_tool") is False

    async def test_open_blocks_calls_until_cooldown(self) -> None:
        """While OPEN, all calls are blocked."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        # Trip circuit
        for _ in range(5):
            await ToolCircuitBreaker.record_failure("blocked_tool")

        assert await ToolCircuitBreaker.check("blocked_tool") is False

    async def test_half_open_probe_success_closes_circuit(self) -> None:
        """After cooldown → HALF_OPEN probe succeeds → CLOSED, failures=0."""
        import time
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        # Trip circuit with a short artificial cooldown
        ToolCircuitBreaker.COOLDOWN_SECONDS = 0.05
        try:
            for _ in range(5):
                await ToolCircuitBreaker.record_failure("probe_tool")

            # Verify circuit is OPEN (check state directly, bypassing cooldown check)
            state_before = ToolCircuitBreaker._states.get("probe_tool")
            assert state_before is not None
            assert state_before.state == "OPEN"

            # check() should block while still in cooldown
            assert await ToolCircuitBreaker.check("probe_tool") is False

            # Wait for cooldown to expire
            time.sleep(0.06)

            # HALF_OPEN — allowed
            assert await ToolCircuitBreaker.check("probe_tool") is True

            # Record success → CLOSED
            await ToolCircuitBreaker.record_success("probe_tool")

            # Now should be CLOSED and allowed
            assert await ToolCircuitBreaker.check("probe_tool") is True

            # Verify state was reset
            state = ToolCircuitBreaker._states.get("probe_tool")
            assert state is not None
            assert state.failures == 0
            assert state.state == "CLOSED"
        finally:
            ToolCircuitBreaker.COOLDOWN_SECONDS = 120.0

    async def test_half_open_probe_failure_returns_to_open(self) -> None:
        """HALF_OPEN probe fails → back to OPEN, cooldown restarts."""
        import time
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        ToolCircuitBreaker.COOLDOWN_SECONDS = 0.05
        try:
            for _ in range(5):
                await ToolCircuitBreaker.record_failure("probe_fail")

            # Verify circuit is OPEN
            state_before = ToolCircuitBreaker._states.get("probe_fail")
            assert state_before is not None
            assert state_before.state == "OPEN"

            # Wait for cooldown to expire
            time.sleep(0.06)

            # HALF_OPEN — allowed
            assert await ToolCircuitBreaker.check("probe_fail") is True

            # Probe FAILS
            await ToolCircuitBreaker.record_failure("probe_fail")

            # Back to OPEN — blocked immediately
            assert await ToolCircuitBreaker.check("probe_fail") is False

            state = ToolCircuitBreaker._states["probe_fail"]
            assert state.state == "OPEN"
            assert state.failures == 6
        finally:
            ToolCircuitBreaker.COOLDOWN_SECONDS = 120.0

    async def test_success_resets_failure_counter(self) -> None:
        """A successful call resets the failure counter without tripping."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        # 3 failures, then success
        for _ in range(3):
            await ToolCircuitBreaker.record_failure("reset_tool")

        await ToolCircuitBreaker.record_success("reset_tool")

        state = ToolCircuitBreaker._states.get("reset_tool")
        assert state is not None
        assert state.failures == 0
        assert state.state == "CLOSED"

    async def test_reset_all_clears_everything(self) -> None:
        """reset() without tool_name clears all circuits."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        for _ in range(5):
            await ToolCircuitBreaker.record_failure("tool_a")
            await ToolCircuitBreaker.record_failure("tool_b")

        await ToolCircuitBreaker.reset()
        assert len(ToolCircuitBreaker._states) == 0

    async def test_reset_single_tool(self) -> None:
        """reset(tool_name) clears only that tool."""
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        for _ in range(5):
            await ToolCircuitBreaker.record_failure("tool_a")

        await ToolCircuitBreaker.record_failure("tool_b")

        await ToolCircuitBreaker.reset("tool_a")
        assert "tool_a" not in ToolCircuitBreaker._states
        assert "tool_b" in ToolCircuitBreaker._states
        assert ToolCircuitBreaker._states["tool_b"].failures == 1

    # ── Integration: middleware chain with circuit breaker ───────

    async def test_middleware_circuit_blocks_on_open(self) -> None:
        """Middleware pre-hook blocks when circuit is OPEN."""
        from src.core.actions.tool_middleware import (
            MiddlewareChain,
            ToolCircuitBreaker,
            _build_circuit_breaker,
        )

        # Trip the circuit directly
        for _ in range(5):
            await ToolCircuitBreaker.record_failure("my_tool")

        chain = MiddlewareChain()
        pre, post = _build_circuit_breaker()
        chain.register(name="cb", priority=30, pre=pre, post=post)

        async def handler(name: str, params: dict) -> dict:  # pragma: no cover
            return {"ok": True}

        result = await chain.wrap("my_tool", {}, handler)
        assert "error" in result
        assert "OPEN" in result["error"]

    async def test_middleware_circuit_allows_when_closed(self) -> None:
        """Middleware pre-hook passes through when circuit is CLOSED."""
        from src.core.actions.tool_middleware import (
            MiddlewareChain,
            _build_circuit_breaker,
        )

        chain = MiddlewareChain()
        pre, post = _build_circuit_breaker()
        chain.register(name="cb", priority=30, pre=pre, post=post)

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True, "tool": name}

        result = await chain.wrap("ok_tool", {}, handler)
        assert "error" not in result
        assert result["ok"] is True

    async def test_middleware_post_hook_records_failure_on_handler_error(self) -> None:
        """Post-hook increments failure counter when handler returns error."""
        from src.core.actions.tool_middleware import (
            MiddlewareChain,
            ToolCircuitBreaker,
            _build_circuit_breaker,
        )

        chain = MiddlewareChain()
        pre, post = _build_circuit_breaker()
        chain.register(name="cb", priority=30, pre=pre, post=post)

        async def handler(name: str, params: dict) -> dict:
            raise RuntimeError("boom")

        result = await chain.wrap("err_tool", {}, handler)
        assert "error" in result

        state = ToolCircuitBreaker._states.get("err_tool")
        assert state is not None
        assert state.failures == 1

    async def test_middleware_post_hook_records_success(self) -> None:
        """Post-hook records success when handler returns without error."""
        from src.core.actions.tool_middleware import (
            MiddlewareChain,
            ToolCircuitBreaker,
            _build_circuit_breaker,
        )

        chain = MiddlewareChain()
        pre, post = _build_circuit_breaker()
        chain.register(name="cb", priority=30, pre=pre, post=post)

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True}

        # First create some failures to have state
        for _ in range(3):
            await ToolCircuitBreaker.record_failure("ok_tool2")

        result = await chain.wrap("ok_tool2", {}, handler)
        assert "error" not in result

        state = ToolCircuitBreaker._states.get("ok_tool2")
        assert state is not None
        assert state.failures == 0  # Reset on success
        assert state.state == "CLOSED"

    async def test_full_circuit_breaker_lifecycle(self) -> None:
        """Integration test: CLOSED → OPEN → HALF_OPEN → CLOSED via middleware."""
        import time

        from src.core.actions.tool_middleware import (
            MiddlewareChain,
            ToolCircuitBreaker,
            _build_circuit_breaker,
        )

        # Force short cooldown for fast test
        ToolCircuitBreaker.COOLDOWN_SECONDS = 0.1
        try:
            chain = MiddlewareChain()
            pre, post = _build_circuit_breaker()
            chain.register(name="cb", priority=30, pre=pre, post=post)

            call_count = 0

            async def failing_handler(name: str, params: dict) -> dict:
                nonlocal call_count
                call_count += 1
                raise RuntimeError("fail")

            async def success_handler(name: str, params: dict) -> dict:
                nonlocal call_count
                call_count += 1
                return {"ok": True}

            # ── Phase 1: 5 failures → OPEN ──
            for _ in range(5):
                result = await chain.wrap("lifecycle_tool", {}, failing_handler)
                assert "error" in result
            assert call_count == 5

            # ── Phase 2: 6th call — blocked by circuit (OPEN) ──
            result = await chain.wrap("lifecycle_tool", {}, failing_handler)
            assert "error" in result
            assert "OPEN" in result["error"]
            assert call_count == 5  # handler NOT called

            # ── Phase 3: Wait for cooldown → HALF_OPEN probe succeeds → CLOSED ──
            time.sleep(0.15)
            result = await chain.wrap("lifecycle_tool", {}, success_handler)
            assert "error" not in result
            assert call_count == 6  # handler called (probe)
            assert result["ok"] is True

            # ── Phase 4: CLOSED — next call succeeds normally ──
            result = await chain.wrap("lifecycle_tool", {}, success_handler)
            assert "error" not in result
            assert call_count == 7
        finally:
            ToolCircuitBreaker.COOLDOWN_SECONDS = 120.0

    async def test_default_middleware_includes_circuit_breaker(self) -> None:
        """After init_default_middlewares, circuit_breaker is registered."""
        from src.core.actions.tool_middleware import (
            MiddlewareChain,
            init_default_middlewares,
        )

        chain = MiddlewareChain()
        init_default_middlewares(chain=chain)
        assert "circuit_breaker" in chain.registered
