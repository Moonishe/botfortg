"""MCP tool: mcp_self_info — статус и диагностика агента."""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)

_START_TIME = time.monotonic()


@tool(
    name="mcp_self_info",
    description="Статус агента: uptime, провайдеры, память, версия",
    category="admin",
    risk="low",
    params={
        "action": "str — status | health | providers | version (default: status)",
    },
)
async def mcp_self_info(action: str = "status") -> dict[str, Any]:
    uptime = time.monotonic() - _START_TIME

    if action == "status":
        try:
            from src.core.actions.tool_registry import tool_registry

            tools_count = len(tool_registry._tools)
        except Exception:
            tools_count = 0
        return {
            "uptime_seconds": round(uptime),
            "uptime_human": f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m",
            "python_version": sys.version.split()[0],
            "tools_count": tools_count,
            "pid": os.getpid(),
        }

    elif action == "health":
        checks = {}
        try:
            from src.db.session import get_session
            from sqlalchemy import text as sql_text

            async with get_session() as session:
                await session.execute(sql_text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {e}"

        try:
            from src.core.actions.vector_store import get_vector_store

            store = await get_vector_store()
            checks["vector_store"] = "ok" if store._dim else "not_initialized"
        except Exception as e:
            checks["vector_store"] = f"error: {e}"

        return {"health": checks, "uptime_seconds": round(uptime)}

    elif action == "providers":
        try:
            from src.db.session import get_session
            from src.db.models._auth import LlmKeySlot
            from sqlalchemy import select

            async with get_session() as session:
                result = await session.execute(
                    select(LlmKeySlot).where(LlmKeySlot.enabled == True)  # noqa: E712
                )
                # .scalars() is required: iterating a raw `select(Entity)`
                # result yields Row(Entity,) tuples, so `s.provider` would
                # raise AttributeError. (list_providers in mcp_self_model uses
                # a tuple-select and indexes r[0]; here we want entity access.)
                providers = [
                    {
                        "provider": s.provider,
                        "model": s.model,
                        "priority": s.priority,
                        "cooldown": str(s.cooldown_until) if s.cooldown_until else None,
                    }
                    for s in result.scalars()
                ]
            return {"providers": providers, "total": len(providers)}
        except Exception as e:
            return {"error": str(e)}

    elif action == "version":
        return {
            "python": sys.version.split()[0],
            "uptime_seconds": round(uptime),
        }

    return {"error": f"Unknown action: {action}"}


# ── Auto-register for MCP exposure ──
from src.core.actions.mcp_expose import expose_to_mcp

expose_to_mcp(
    "mcp_self_info",
    description="Agent diagnostics: status, health, providers, version",
)
