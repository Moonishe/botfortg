"""Tests for ToolMetricsCollector and middleware integration."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.observability.tool_metrics import (
    ToolMetricsCollector,
    ToolMetricsSnapshot,
    tool_metrics,
)


# ===================================================================
# ToolMetricsSnapshot — unit tests
# ===================================================================


class TestToolMetricsSnapshot:
    """Pure unit tests for the snapshot dataclass."""

    def test_defaults(self) -> None:
        snap = ToolMetricsSnapshot(tool_name="test_tool")
        assert snap.tool_name == "test_tool"
        assert snap.call_count == 0
        assert snap.error_count == 0
        assert snap.total_latency_ms == 0.0
        assert snap.avg_latency_ms == 0.0
        assert snap.last_called_at == 0.0
        assert snap.success_rate == 0.0

    def test_success_rate_all_ok(self) -> None:
        snap = ToolMetricsSnapshot(tool_name="t", call_count=10, error_count=0)
        assert snap.success_rate == 1.0

    def test_success_rate_half(self) -> None:
        snap = ToolMetricsSnapshot(tool_name="t", call_count=10, error_count=5)
        assert snap.success_rate == 0.5

    def test_success_rate_all_errors(self) -> None:
        snap = ToolMetricsSnapshot(tool_name="t", call_count=5, error_count=5)
        assert snap.success_rate == 0.0

    def test_success_rate_zero_calls(self) -> None:
        snap = ToolMetricsSnapshot(tool_name="t")
        assert snap.success_rate == 0.0


# ===================================================================
# ToolMetricsCollector — unit tests
# ===================================================================


class TestToolMetricsCollector:
    """Tests for the singleton collector: record, query, reset."""

    async def test_record_first_call(self) -> None:
        collector = ToolMetricsCollector()
        await collector.record_call("mcp_search", latency_ms=100.0, success=True)

        snap = await collector.get_snapshot("mcp_search")
        assert snap is not None
        assert snap.tool_name == "mcp_search"
        assert snap.call_count == 1
        assert snap.error_count == 0
        assert snap.total_latency_ms == 100.0
        assert snap.avg_latency_ms == 100.0
        assert snap.last_called_at > 0

    async def test_record_multiple_calls(self) -> None:
        collector = ToolMetricsCollector()
        await collector.record_call("mcp_search", latency_ms=100.0, success=True)
        await collector.record_call("mcp_search", latency_ms=50.0, success=True)
        await collector.record_call("mcp_search", latency_ms=30.0, success=False)

        snap = await collector.get_snapshot("mcp_search")
        assert snap is not None
        assert snap.call_count == 3
        assert snap.error_count == 1
        assert snap.total_latency_ms == 180.0
        assert snap.avg_latency_ms == 60.0
        assert snap.success_rate == 2 / 3

    async def test_get_nonexistent_tool(self) -> None:
        collector = ToolMetricsCollector()
        snap = await collector.get_snapshot("nonexistent")
        assert snap is None

    async def test_get_all_snapshots_sorted(self) -> None:
        collector = ToolMetricsCollector()
        await collector.record_call("tool_a", latency_ms=10.0, success=True)
        await collector.record_call("tool_b", latency_ms=20.0, success=True)
        await collector.record_call("tool_b", latency_ms=30.0, success=True)
        await collector.record_call("tool_c", latency_ms=40.0, success=True)
        await collector.record_call("tool_c", latency_ms=50.0, success=True)
        await collector.record_call("tool_c", latency_ms=60.0, success=True)

        snaps = await collector.get_all_snapshots()
        assert len(snaps) == 3
        # Sorted by call_count descending
        assert snaps[0].tool_name == "tool_c"
        assert snaps[0].call_count == 3
        assert snaps[1].tool_name == "tool_b"
        assert snaps[1].call_count == 2
        assert snaps[2].tool_name == "tool_a"
        assert snaps[2].call_count == 1

    async def test_reset_clears_all(self) -> None:
        collector = ToolMetricsCollector()
        await collector.record_call("tool_x", latency_ms=10.0, success=True)
        await collector.record_call("tool_y", latency_ms=20.0, success=True)

        assert await collector.get_snapshot("tool_x") is not None
        assert len(await collector.get_all_snapshots()) == 2

        await collector.reset()

        assert await collector.get_snapshot("tool_x") is None
        assert await collector.get_snapshot("tool_y") is None
        assert len(await collector.get_all_snapshots()) == 0

    async def test_snapshot_is_independent_copy(self) -> None:
        collector = ToolMetricsCollector()
        await collector.record_call("tool_x", latency_ms=100.0, success=True)

        snap1 = await collector.get_snapshot("tool_x")
        assert snap1 is not None
        assert snap1.call_count == 1

        await collector.record_call("tool_x", latency_ms=50.0, success=True)

        # snap1 should be unchanged (it's a copy)
        assert snap1.call_count == 1
        snap2 = await collector.get_snapshot("tool_x")
        assert snap2 is not None
        assert snap2.call_count == 2


# ===================================================================
# ToolMetricsCollector — eviction
# ===================================================================


class TestToolMetricsEviction:
    """Tests for the MAX_RETAINED_TOOLS eviction behaviour."""

    async def test_evicts_oldest_on_overflow(self) -> None:
        collector = ToolMetricsCollector(max_retained_tools=3)

        # Fill up to 3 tools
        await collector.record_call("tool_1", latency_ms=10.0, success=True)
        await collector.record_call("tool_2", latency_ms=10.0, success=True)
        await collector.record_call("tool_3", latency_ms=10.0, success=True)

        assert len(await collector.get_all_snapshots()) == 3

        # Add a 4th — should evict the oldest (tool_1)
        await collector.record_call("tool_4", latency_ms=10.0, success=True)

        snaps = await collector.get_all_snapshots()
        assert len(snaps) == 3
        names = {s.tool_name for s in snaps}
        assert "tool_1" not in names  # evicted
        assert names == {"tool_2", "tool_3", "tool_4"}


# ===================================================================
# Concurrent access
# ===================================================================


class TestToolMetricsConcurrency:
    """Smoke test for concurrent record_call calls."""

    async def test_concurrent_records(self) -> None:
        collector = ToolMetricsCollector()

        async def record_batch(tool: str, count: int, latency: float) -> None:
            for _ in range(count):
                await collector.record_call(tool, latency_ms=latency, success=True)

        await asyncio.gather(
            record_batch("tool_a", 100, 1.0),
            record_batch("tool_b", 100, 2.0),
            record_batch("tool_c", 100, 3.0),
        )

        snap_a = await collector.get_snapshot("tool_a")
        snap_b = await collector.get_snapshot("tool_b")
        snap_c = await collector.get_snapshot("tool_c")

        assert snap_a is not None and snap_a.call_count == 100
        assert snap_b is not None and snap_b.call_count == 100
        assert snap_c is not None and snap_c.call_count == 100


# ===================================================================
# Middleware integration
# ===================================================================


class TestMiddlewareMetricsIntegration:
    """Verify the middleware post-hook records into ToolMetricsCollector."""

    async def test_post_hook_records_metrics(self) -> None:
        from src.core.actions.tool_middleware import (
            MiddlewareChain,
            _build_tool_metrics_post,
        )
        from src.core.observability.tool_metrics import tool_metrics as tm

        # Use a fresh collector to avoid cross-test pollution
        await tm.reset()

        chain = MiddlewareChain()
        chain.register(name="metrics", priority=90, post=_build_tool_metrics_post())

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True, "tool": name}

        await chain.wrap("test_tool", {}, handler)

        snap = await tm.get_snapshot("test_tool")
        assert snap is not None
        assert snap.call_count == 1
        assert snap.error_count == 0
        assert snap.avg_latency_ms > 0

    async def test_post_hook_records_error(self) -> None:
        from src.core.actions.tool_middleware import (
            MiddlewareChain,
            _build_tool_metrics_post,
        )
        from src.core.observability.tool_metrics import tool_metrics as tm

        await tm.reset()

        chain = MiddlewareChain()
        chain.register(name="metrics", priority=90, post=_build_tool_metrics_post())

        async def handler(name: str, params: dict) -> dict:
            return {"error": "something went wrong"}

        await chain.wrap("failing_tool", {}, handler)

        snap = await tm.get_snapshot("failing_tool")
        assert snap is not None
        assert snap.call_count == 1
        assert snap.error_count == 1
        assert snap.success_rate == 0.0

    async def test_post_hook_sets_latency_meta(self) -> None:
        from src.core.actions.tool_middleware import (
            MiddlewareChain,
            ToolContext,
            _build_tool_metrics_post,
        )

        chain = MiddlewareChain()
        chain.register(name="metrics", priority=90, post=_build_tool_metrics_post())

        # Capture the context after post-hook
        captured_ctx: ToolContext | None = None

        async def capture_post(ctx: ToolContext) -> None:
            nonlocal captured_ctx
            captured_ctx = ctx

        chain.register(name="capture", priority=100, post=capture_post)

        async def handler(name: str, params: dict) -> dict:
            return {"ok": True}

        await chain.wrap("meta_tool", {}, handler)

        assert captured_ctx is not None
        assert "latency_seconds" in captured_ctx.meta
        assert "latency_ms" in captured_ctx.meta
        assert captured_ctx.meta["latency_seconds"] >= 0
        assert captured_ctx.meta["latency_ms"] >= 0
