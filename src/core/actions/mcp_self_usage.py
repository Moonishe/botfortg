"""MCP tool: mcp_self_usage — проверка расхода API (токены, стоимость, лимиты)."""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import tool
from src.core.observability.account_usage import get_tracker

logger = logging.getLogger(__name__)


@tool(
    name="mcp_self_usage",
    description="API usage: токены, стоимость, лимиты — отчёт или проверка",
    category="admin",
    risk="low",
    params={
        "action": "str — report | limits (default: report)",
    },
)
async def mcp_self_usage(action: str = "report") -> dict[str, Any]:
    """Return usage report or limit check for the current API account.

    ``report`` — человекочитаемый отчёт за сегодня/неделю/месяц.
    ``limits`` — проверка против daily=100k / monthly=1M токенов.
    """
    tracker = get_tracker()

    if action == "report":
        report = await tracker.get_usage_report()
        return {"report": report}

    if action == "limits":
        limits = await tracker.check_limits()
        return {"limits": limits}

    return {"error": f"Unknown action: {action!r}. Valid: report, limits"}


@tool(
    name="mcp_metrics",
    description="Tool execution metrics: list all, get by name, reset",
    category="admin",
    risk="low",
    params={
        "action": "str — list | tool | reset (default: list)",
        "tool_name": "str — имя инструмента для action=tool (опционально)",
    },
)
async def mcp_metrics(action: str = "list", tool_name: str = "") -> dict[str, Any]:
    """Query or reset per-tool execution metrics collected by the middleware.

    ``list`` — all tool snapshots sorted by call count descending.
    ``tool`` — single snapshot for *tool_name*.
    ``reset`` — clear all metrics.
    """
    from src.core.observability.tool_metrics import tool_metrics

    if action == "list":
        snaps = await tool_metrics.get_all_snapshots()
        return {
            "metrics": [
                {
                    "tool_name": s.tool_name,
                    "call_count": s.call_count,
                    "error_count": s.error_count,
                    "avg_latency_ms": s.avg_latency_ms,
                    "total_latency_ms": s.total_latency_ms,
                    "success_rate": round(s.success_rate, 4),
                    "last_called_at": s.last_called_at,
                }
                for s in snaps
            ],
            "total_tools": len(snaps),
        }

    if action == "tool":
        if not tool_name:
            return {"error": "tool_name required for action='tool'"}
        snap = await tool_metrics.get_snapshot(tool_name)
        if snap is None:
            return {"error": f"Unknown tool: {tool_name!r}"}
        return {
            "tool_name": snap.tool_name,
            "call_count": snap.call_count,
            "error_count": snap.error_count,
            "avg_latency_ms": snap.avg_latency_ms,
            "total_latency_ms": snap.total_latency_ms,
            "success_rate": round(snap.success_rate, 4),
            "last_called_at": snap.last_called_at,
        }

    if action == "reset":
        await tool_metrics.reset()
        return {"reset": True, "message": "All tool metrics cleared"}

    return {"error": f"Unknown action: {action!r}. Valid: list, tool, reset"}


# ── Auto-register for MCP exposure ──
from src.core.actions.mcp_expose import expose_to_mcp

expose_to_mcp(
    "mcp_self_usage",
    description="API usage: token & cost report, limit checks for today/week/month",
)

expose_to_mcp(
    "mcp_metrics",
    description="Tool metrics: list all tools with latency/error stats, inspect one, reset",
)
