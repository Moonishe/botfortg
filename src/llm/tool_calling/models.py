"""Data models for LLM tool calling."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def safe_parse_tool_args(raw_arguments: str) -> dict[str, Any]:
    """Parse tool call arguments JSON safely.

    Returns an empty dict on malformed JSON or null/non-dict values.
    Prevents crashing the tool-calling loop on unexpected LLM output.
    """
    try:
        parsed = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Malformed tool call arguments JSON: %s", exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Tool call arguments is not a dict: %r", type(parsed).__name__)
        return {}
    return parsed


@dataclass(frozen=True)
class ToolCall:
    """A tool call requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolCallResult:
    """Result of executing a tool call."""

    tool_call_id: str
    name: str
    result: dict[str, Any] | None = None
    error: str | None = None

    def format_for_llm(self) -> str:
        """Format the result as a string suitable for the LLM."""
        if self.error:
            return json.dumps({"error": self.error}, ensure_ascii=False)
        if self.result is not None:
            return json.dumps(self.result, ensure_ascii=False)
        return json.dumps({"ok": True}, ensure_ascii=False)


@dataclass(frozen=True)
class ToolDefinition:
    """A tool definition to send to the LLM."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the tool's parameters


@dataclass
class ChatResponse:
    """Response from a chat completion with optional tool calls."""

    text: str
    tool_calls: list[ToolCall] | None = None
    finish_reason: str = "stop"
