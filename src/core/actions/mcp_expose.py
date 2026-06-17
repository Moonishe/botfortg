"""MCP Tool Auto-Discovery — self-registering tools for MCP exposure.

Usage — at module level in any ``mcp_*.py``::

    from src.core.actions.mcp_expose import expose_to_mcp
    expose_to_mcp("mcp_self_model")  # auto-generates JSON Schema from tool_registry

Or with a hand-crafted schema::

    expose_to_mcp("my_tool", input_schema={
        "type": "object",
        "properties": {"arg": {"type": "string"}},
        "required": ["arg"],
    })
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Registry ────────────────────────────────────────────────────────────────

_mcp_exposed: dict[str, dict[str, Any]] = {}


def expose_to_mcp(
    tool_name: str,
    *,
    input_schema: dict[str, Any] | None = None,
    description: str | None = None,
) -> None:
    """Register a tool for MCP exposure.

    If *input_schema* is omitted, it's auto-generated from the tool's
    ``ToolSpec.params`` in ``tool_registry``.

    Safe to call multiple times — duplicate registrations are skipped.
    """
    if tool_name in _mcp_exposed:
        return  # already registered

    if input_schema is None:
        try:
            from src.core.actions.tool_registry import tool_registry
        except Exception:
            logger.exception(
                "expose_to_mcp: cannot import tool_registry for %r", tool_name
            )
            return

        try:
            spec = tool_registry.get(tool_name)
        except Exception:
            logger.exception(
                "expose_to_mcp: tool_registry.get(%r) raised exception", tool_name
            )
            return

        if spec is None:
            logger.warning("expose_to_mcp: tool %r not in registry", tool_name)
            return
        input_schema = spec.input_schema or _params_to_json_schema(spec.params)
        if description is None:
            description = spec.description

    _mcp_exposed[tool_name] = {
        "description": description or tool_name,
        "inputSchema": input_schema,
    }


def get_mcp_exposed() -> dict[str, dict[str, Any]]:
    """Return the merged MCP tool schemas for ``tools/list`` response.

    Merges auto-discovered tools with the hand-curated set.
    """
    from src.core.mcp_server import EXPOSED_TOOLS  # legacy curated tools

    merged = dict(EXPOSED_TOOLS)  # hand-curated takes priority
    for name, schema in _mcp_exposed.items():
        if name not in merged:
            merged[name] = schema
    return merged


# ── Schema generation helpers ────────────────────────────────────────────────

_TYPE_MAP: dict[str, str] = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "dict": "object",
    "list": "array",
}


def _params_to_json_schema(
    params: dict[str, str], required: list[str] | None = None
) -> dict[str, Any]:
    """Convert a ``ToolSpec.params`` dict to JSON Schema.

    Example::

        {"query": "str", "limit": "int|None"}
        → {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        }, "required": ["query"]}
    """
    properties: dict[str, Any] = {}
    required_keys: list[str] = []

    for name, type_hint in params.items():
        is_optional = (
            "|None" in type_hint
            or "None|" in type_hint
            or "optional" in type_hint.lower()
        )
        base_type = (
            type_hint.replace("|None", "")
            .replace("None|", "")
            .strip()
            .split("|")[0]
            .strip()
        )

        json_type = _TYPE_MAP.get(base_type, "string")
        prop: dict[str, Any] = {"type": json_type}

        # Integer range
        if json_type == "integer":
            prop["default"] = 0
        elif json_type == "number":
            prop["default"] = 0.0

        properties[name] = prop
        if not is_optional:
            required_keys.append(name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required_keys:
        schema["required"] = required_keys
    return schema
