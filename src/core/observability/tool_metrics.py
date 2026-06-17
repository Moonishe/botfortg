"""Tool Metrics Collector — runtime observability for tool execution.

Tracks per-tool call counts, errors, latency in a thread-safe singleton.
Designed for the middleware post-hook in :mod:`src.core.actions.tool_middleware`
and for MCP introspection via :mod:`src.core.actions.mcp_self_usage`.

Usage::

    from src.core.observability.tool_metrics import tool_metrics

    tool_metrics.record_call("mcp_search", latency_ms=45.2, success=True)
    snap = tool_metrics.get_snapshot("mcp_search")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolMetricsSnapshot:
    """Immutable snapshot of per-tool metrics at one point in time."""

    tool_name: str
    call_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    last_called_at: float = 0.0

    @property
    def success_rate(self) -> float:
        """Return 0.0—1.0 success rate, or 0.0 if no calls."""
        if self.call_count == 0:
            return 0.0
        return (self.call_count - self.error_count) / self.call_count


class ToolMetricsCollector:
    """Singleton collector for tool execution metrics.

    All mutation methods are protected by :class:`asyncio.Lock`.

    Args:
        max_retained_tools: Maximum number of distinct tools to track.
            When exceeded the least-recently-called tool is evicted.
            Default 200.
    """

    def __init__(self, max_retained_tools: int = 200) -> None:
        self._metrics: dict[str, ToolMetricsSnapshot] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._max_retained_tools: int = max_retained_tools

    # ── Record ────────────────────────────────────────────────────

    async def record_call(
        self, tool_name: str, latency_ms: float, success: bool
    ) -> None:
        """Record one tool call.

        Args:
            tool_name: Tool identifier (e.g. ``"mcp_search"``).
            latency_ms: Wall-clock latency in milliseconds.
            success: ``True`` if the call succeeded, ``False`` if it errored.
        """
        async with self._lock:
            snap = self._metrics.get(tool_name)
            if snap is None:
                if len(self._metrics) >= self._max_retained_tools:
                    # Evict the least-recently-called tool
                    oldest = min(
                        self._metrics.items(),
                        key=lambda kv: kv[1].last_called_at,
                    )
                    del self._metrics[oldest[0]]
                    logger.debug("Evicted oldest tool metrics: %s", oldest[0])

                self._metrics[tool_name] = ToolMetricsSnapshot(
                    tool_name=tool_name,
                    call_count=1,
                    error_count=0 if success else 1,
                    total_latency_ms=latency_ms,
                    avg_latency_ms=latency_ms,
                    last_called_at=time.monotonic(),
                )
            else:
                new_count = snap.call_count + 1
                new_errors = snap.error_count + (0 if success else 1)
                new_total = snap.total_latency_ms + latency_ms
                self._metrics[tool_name] = ToolMetricsSnapshot(
                    tool_name=tool_name,
                    call_count=new_count,
                    error_count=new_errors,
                    total_latency_ms=new_total,
                    avg_latency_ms=round(new_total / new_count, 4),
                    last_called_at=time.monotonic(),
                )

    # ── Query ─────────────────────────────────────────────────────

    async def get_snapshot(self, tool_name: str) -> ToolMetricsSnapshot | None:
        """Return a *copy* of the snapshot for *tool_name*, or ``None``."""
        async with self._lock:
            snap = self._metrics.get(tool_name)
            if snap is None:
                return None
            return ToolMetricsSnapshot(
                tool_name=snap.tool_name,
                call_count=snap.call_count,
                error_count=snap.error_count,
                total_latency_ms=snap.total_latency_ms,
                avg_latency_ms=snap.avg_latency_ms,
                last_called_at=snap.last_called_at,
            )

    async def get_all_snapshots(self) -> list[ToolMetricsSnapshot]:
        """Return copies of all current snapshots, sorted by *call_count* desc."""
        async with self._lock:
            return sorted(
                (
                    ToolMetricsSnapshot(
                        tool_name=s.tool_name,
                        call_count=s.call_count,
                        error_count=s.error_count,
                        total_latency_ms=s.total_latency_ms,
                        avg_latency_ms=s.avg_latency_ms,
                        last_called_at=s.last_called_at,
                    )
                    for s in self._metrics.values()
                ),
                key=lambda s: s.call_count,
                reverse=True,
            )

    async def reset(self) -> None:
        """Clear all collected metrics."""
        async with self._lock:
            self._metrics.clear()
            logger.info("Tool metrics reset")


# ── Module-level singleton ────────────────────────────────────────

tool_metrics = ToolMetricsCollector()
