"""MCP tool: mcp_self_config — runtime config overrides."""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import tool
from src.config import settings

logger = logging.getLogger(__name__)

_runtime_overrides: dict[str, Any] = {}

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
        try:
            coerced = _coerce(key, value)
        except (ValueError, TypeError):
            return {"error": f"Invalid value for {key}: {value}"}
        _runtime_overrides[key] = coerced
        logger.info("Config override: %s = %s", key, coerced)
        if key == "maestro_model":
            try:
                from src.llm.router import _CIRCUIT_BREAKERS

                _CIRCUIT_BREAKERS.clear()
            except Exception:
                pass
        return {"ok": True, "key": key, "value": coerced}

    elif action == "list":
        items = {}
        for k in _COERCE_MAP:
            items[k] = _runtime_overrides.get(k) or getattr(settings, k, None)
        return {"config": items, "overrides": dict(_runtime_overrides)}

    elif action == "reset":
        if key:
            _runtime_overrides.pop(key, None)
            return {"ok": True, "key": key, "reset": True}
        _runtime_overrides.clear()
        return {"ok": True, "reset_all": True}

    return {"error": f"Unknown action: {action}"}
