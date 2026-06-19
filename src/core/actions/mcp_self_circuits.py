"""MCP tool: mcp_self_circuits — circuit breaker introspection and control."""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="mcp_self_circuits",
    description="Circuit breaker introspection: status, history, reset circuits",
    category="admin",
    risk="medium",
    requires_confirmation=True,
    params={
        "action": "str — status | tool | history | reset (default: status)",
        "tool_name": "str — tool name for action=tool|reset (optional)",
    },
)
async def mcp_self_circuits(
    action: str = "status",
    tool_name: str = "",
    *,
    _confirmed: bool = False,
) -> dict[str, Any]:
    """Introspect and control circuit breaker state.

    ``status`` — all circuit states with cooldown remaining.
    ``tool`` — specific tool's state + recent transition history.
    ``history`` — recent transitions across all tools.
    ``reset`` — reset a specific tool's circuit (requires confirmation).
    """
    from src.core.actions.tool_middleware import ToolCircuitBreaker
    from src.core.observability.circuit_telemetry import circuit_telemetry

    # Validate tool_name: prevent injection/DoS via untrusted MCP params
    _VALID_ACTIONS = frozenset({"status", "tool", "history", "reset"})
    if action not in _VALID_ACTIONS:
        return {
            "error": f"Unknown action: {action!r}. Valid: {', '.join(sorted(_VALID_ACTIONS))}"
        }
    if tool_name and (len(tool_name) > 200 or "/" in tool_name or "\\" in tool_name):
        return {"error": "tool_name is too long or contains path separators"}

    if action == "status":
        report = await circuit_telemetry.get_report()
        return report

    if action == "tool":
        if not tool_name:
            return {"error": "tool_name required for action='tool'"}
        status = await circuit_telemetry.get_tool_status(tool_name)
        return status

    if action == "history":
        all_tool_names = await circuit_telemetry.get_all_tool_names()
        history_entries: list[dict[str, Any]] = []
        for name in sorted(all_tool_names):
            events = await circuit_telemetry.get_history(name, limit=5)
            for e in events:
                history_entries.append(
                    {
                        "tool_name": e.tool_name,
                        "from_state": e.from_state,
                        "to_state": e.to_state,
                        "timestamp": e.timestamp,
                        "reason": e.reason,
                    }
                )
        # Sort by timestamp descending, most recent first
        history_entries.sort(key=lambda x: x["timestamp"], reverse=True)
        return {
            "total_entries": len(history_entries),
            "history": history_entries[:20],
        }

    if action == "reset":
        if not _confirmed:
            return {
                "error": "requires confirmation",
                "message": "Reset circuit breaker — confirm to proceed",
            }
        if not tool_name:
            return {"error": "tool_name required for action='reset'"}
        await ToolCircuitBreaker.reset(tool_name)
        return {
            "reset": True,
            "tool_name": tool_name,
            "message": f"Circuit for {tool_name!r} manually reset",
        }

    return {"error": f"Unexpected action: {action!r}"}  # unreachable: validated above


# ── Auto-register for MCP exposure ──
from src.core.actions.mcp_expose import expose_to_mcp

expose_to_mcp(
    "mcp_self_circuits",
    description="Circuit breaker introspection: status, tool details, history, manual reset",
)
