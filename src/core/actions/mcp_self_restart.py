"""MCP tool: mcp_self_restart — перезагрузка компонентов агента."""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="mcp_self_restart",
    description="Перезагрузка компонентов: tools, userbot, bot, cache",
    category="admin",
    risk="high",
    requires_confirmation=True,
    params={
        "action": "str — reload_tools | restart_userbot | restart_bot | clear_cache | list_components",
    },
)
async def mcp_self_restart(action: str) -> dict[str, Any]:
    if action == "reload_tools":
        try:
            from src.core.actions import register_builtin_tools
            import asyncio

            await asyncio.to_thread(lambda: register_builtin_tools(force=True))
            from src.core.actions.tool_registry import tool_registry

            count = len(tool_registry._tools)
            return {"ok": True, "tools_reloaded": count}
        except Exception as e:
            return {"error": str(e)}

    elif action == "restart_userbot":
        try:
            from src.userbot.manager import _MANAGER_SINGLETON

            mgr = _MANAGER_SINGLETON
            if mgr is None:
                return {"error": "UserbotManager not initialized"}
            await mgr.shutdown()
            await mgr.restore_all()
            return {"ok": True, "clients": len(mgr._clients)}
        except Exception as e:
            return {"error": str(e)}

    elif action == "restart_bot":
        return {
            "ok": True,
            "note": "Bot restart requires process restart. Use admin_mode shell: mcp_shell command='docker restart telegram-assistant' admin_mode=true",
        }

    elif action == "clear_cache":
        cleared: list[str] = []
        errors: list[str] = []
        # Circuit-breaker reset — never fatal, but report if it fails.
        try:
            from src.llm.provider_manager import (
                _CIRCUIT_BREAKERS,
                _CIRCUIT_BREAKERS_LOCK,
            )

            if _CIRCUIT_BREAKERS_LOCK is not None:
                async with _CIRCUIT_BREAKERS_LOCK:
                    _CIRCUIT_BREAKERS.clear()
            # else: locks not initialized — skip, nothing to clear
            cleared.append("circuit_breakers")
        except Exception as e:
            errors.append(f"circuit_breakers: {e}")
        # FTS5 connection close — never fatal, but report if it fails.
        try:
            from src.core.actions.tool_registry import tool_registry

            if hasattr(tool_registry, "_fts5_conn"):
                try:
                    tool_registry._fts5_conn.close()
                except Exception as e:
                    errors.append(f"fts5_close: {e}")
                del tool_registry._fts5_conn
            cleared.append("tool_search_cache")
        except Exception as e:
            errors.append(f"tool_search_cache: {e}")
        if errors:
            return {
                "ok": False,
                "cleared": cleared,
                "errors": errors,
                "note": "partial failure — see errors list",
            }
        return {"ok": True, "cleared": cleared}

    elif action == "list_components":
        return {
            "components": {
                "tools": "reload_tools",
                "userbot": "restart_userbot",
                "bot": "restart_bot (process restart via shell)",
                "cache": "clear_cache",
            }
        }

    return {"error": f"Unknown action: {action}"}


# ── Auto-register for MCP exposure ──
from src.core.actions.mcp_expose import expose_to_mcp

expose_to_mcp(
    "mcp_self_restart",
    description="Restart components: reload_tools, restart_userbot, clear_cache",
)
