"""MCP tool: mcp_self_config — runtime config overrides."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.core.actions.tool_registry import tool
from src.config import settings

logger = logging.getLogger(__name__)

_runtime_overrides: dict[str, Any] = {}
_runtime_overrides_lock = asyncio.Lock()

_COERCE_MAP = {
    "use_heavy_model": bool,
    "auto_reply_enabled": bool,
    "auto_reply_mode": str,
    "auto_reply_text": str,
    "maestro_model": str,
    "voice_transcription_mode": str,
    "news_enabled": bool,
    "digest_enabled": bool,
    "memory_mode": str,
    "rag_enabled": bool,
    "smart_digest_interval_min": int,
}


def _coerce(key: str, value: str) -> Any:
    target = _COERCE_MAP.get(key, str)
    if target is bool:
        return value.lower() in ("true", "1", "yes", "on")
    if target is int:
        return int(value)
    return value


@tool(
    name="mcp_self_config",
    description="Просмотр и изменение runtime-настроек агента",
    category="admin",
    risk="medium",
    params={
        "action": "str — get | set | list | reset",
        "key": "str — имя настройки",
        "value": "str — новое значение (для set)",
    },
)
async def mcp_self_config(
    action: str,
    key: str = "",
    value: str = "",
) -> dict[str, Any]:
    if action == "get":
        if not key:
            return {"error": "key is required"}
        if key not in _COERCE_MAP:
            return {"error": f"Key {key!r} is not allowed"}
        async with _runtime_overrides_lock:
            val = _runtime_overrides.get(key)
        if val is not None:
            return {"key": key, "value": val, "source": "runtime_override"}
        try:
            val = getattr(settings, key, None)
            return {"key": key, "value": val, "source": "settings"}
        except Exception:
            return {"error": f"Unknown key: {key}"}

    elif action == "set":
        if not key:
            return {"error": "key is required"}
        if key not in _COERCE_MAP:
            return {"error": f"Key {key!r} is not allowed"}
        try:
            coerced = _coerce(key, value)
        except (ValueError, TypeError):
            return {"error": f"Invalid value for {key}: {value}"}
        async with _runtime_overrides_lock:
            _runtime_overrides[key] = coerced
        logger.info("Config override: %s = %s", key, coerced)
        if key == "maestro_model":
            try:
                from src.llm.provider_manager import (
                    _CIRCUIT_BREAKERS,
                    _CIRCUIT_BREAKERS_LOCK,
                )

                if _CIRCUIT_BREAKERS_LOCK is not None:
                    async with _CIRCUIT_BREAKERS_LOCK:
                        _CIRCUIT_BREAKERS.clear()
                # else: locks not initialized — skip, nothing to clear
            except Exception:
                logger.warning("Failed to clear circuit breakers", exc_info=True)
        return {"ok": True, "key": key, "value": coerced}

    elif action == "list":
        async with _runtime_overrides_lock:
            items = {}
            for k in _COERCE_MAP:
                items[k] = (
                    _runtime_overrides[k]
                    if k in _runtime_overrides
                    else getattr(settings, k, None)
                )
            overrides = dict(_runtime_overrides)
        return {"config": items, "overrides": overrides}

    elif action == "reset":
        async with _runtime_overrides_lock:
            if key:
                _runtime_overrides.pop(key, None)
                return {"ok": True, "key": key, "reset": True}
            _runtime_overrides.clear()
            return {"ok": True, "reset_all": True}

    return {"error": f"Unknown action: {action}"}


# ── Auto-register for MCP exposure ──
from src.core.actions.mcp_expose import expose_to_mcp

expose_to_mcp(
    "mcp_self_config",
    description="Runtime config overrides: get, set, list, reset",
)
