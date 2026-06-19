"""Adapter that bridges ToolRegistry to LLM tool-calling schemas."""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import ToolRegistry, ToolSpec, tool_registry
from src.llm.tool_calling.models import ToolCall, ToolCallResult, ToolDefinition

logger = logging.getLogger(__name__)


def _params_to_json_schema(params: dict[str, str]) -> dict[str, Any]:
    """Generate a minimal JSON Schema from a ToolSpec params dict.

    Example: ``{"query": "str", "limit": "int|None"}`` →
    ``{"type": "object", "properties": {...}, "required": [...]}``
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, type_str in params.items():
        if name in ("_confirmed", "_admin_confirmed"):  # skip internal params
            continue
        types = [t.strip() for t in type_str.split("|")]
        json_types: list[str] = []
        nullable = False
        for t in types:
            tl = t.lower()
            if tl == "none":
                nullable = True
            elif tl == "str":
                json_types.append("string")
            elif tl in ("int", "float", "number"):
                json_types.append("number")
            elif tl in ("bool", "boolean"):
                json_types.append("boolean")
            elif tl in ("list", "array"):
                json_types.append("array")
            elif tl in ("dict", "object"):
                json_types.append("object")
            else:
                json_types.append("string")
        prop: dict[str, Any] = (
            {"type": json_types[0]} if len(json_types) == 1 else {"type": "string"}
        )
        if nullable:
            if "null" not in prop.setdefault("type", []):
                if isinstance(prop["type"], list):
                    prop["type"].append("null")
                else:
                    prop["type"] = [prop["type"], "null"]
        if not nullable:
            required.append(name)
        properties[name] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


class ToolRegistryAdapter:
    """Adapt ToolRegistry to LLM tool-calling format.

    Converts ToolSpec → ToolDefinition (OpenAI-compatible JSON Schema)
    and delegates execution to ToolRegistry.execute().
    """

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry or tool_registry

    def get_tool_definitions(
        self,
        *,
        available_only: bool = True,
        names: list[str] | None = None,
        categories: list[str] | None = None,
    ) -> list[ToolDefinition]:
        """Get tool definitions suitable for sending to an LLM.

        Returns ToolDefinition objects with JSON Schema parameters.
        """
        if names:
            specs = []
            for name in names:
                spec = self._registry.get(name)
                if spec and (not available_only or self._registry.is_available(name)):
                    specs.append(spec)
        elif categories:
            cats = self._registry.list_by_category(available_only=available_only)
            specs = []
            for cat in categories:
                specs.extend(cats.get(cat, []))
        else:
            specs = (
                self._registry.get_available_tools()
                if available_only
                else list(self._registry._tools.values())
            )

        definitions: list[ToolDefinition] = []
        for spec in specs:
            if spec.input_schema:
                parameters = spec.input_schema
            elif spec.params:
                parameters = _params_to_json_schema(spec.params)
            else:
                parameters = {"type": "object", "properties": {}, "required": []}
            definitions.append(
                ToolDefinition(
                    name=spec.name,
                    description=spec.description,
                    parameters=parameters,
                )
            )
        return definitions

    async def execute(self, tool_call: ToolCall) -> ToolCallResult:
        """Execute a tool call via the underlying ToolRegistry."""
        try:
            # Strip confirmation keys to prevent LLM bypass of confirmation gate
            args = dict(tool_call.arguments)
            args.pop("_confirmed", None)
            args.pop("_admin_confirmed", None)
            result = await self._registry.execute(tool_call.name, **args)
            return ToolCallResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                result=result,
            )
        except Exception as exc:
            logger.exception("Tool %r execution failed", tool_call.name)
            return ToolCallResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                error=str(exc),
            )
