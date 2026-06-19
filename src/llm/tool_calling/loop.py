"""Tool-calling loop — iterate LLM + tool execution until stop."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from src.llm.base import ChatMessage
from src.llm.tool_calling.models import (
    ChatResponse,
    ToolCall,
    ToolCallResult,
    ToolDefinition,
)
from src.llm.tool_calling.registry_adapter import ToolRegistryAdapter

logger = logging.getLogger(__name__)


@dataclass
class ToolCallingLoop:
    """Iterative LLM tool-calling loop.

    Sends messages + tool definitions to the LLM, executes any requested
    tool calls via ToolRegistryAdapter, and repeats until the LLM returns
    a text response without tool calls (or max_iterations is reached).
    """

    adapter: ToolRegistryAdapter = field(default_factory=ToolRegistryAdapter)
    max_iterations: int = 10
    max_concurrent_tools: int = 10

    def __post_init__(self) -> None:
        self._tool_semaphore = asyncio.Semaphore(self.max_concurrent_tools)

    async def _execute_with_semaphore(
        self, adapter: ToolRegistryAdapter, tc: ToolCall
    ) -> ToolCallResult:
        """Execute a single tool call, gated by the semaphore."""
        async with self._tool_semaphore:
            return await adapter.execute(tc)

    async def iterate(
        self,
        provider: Any,
        messages: list[ChatMessage],
        *,
        tool_names: list[str] | None = None,
        tool_categories: list[str] | None = None,
        task_type: str = "default",
    ) -> ChatResponse:
        """Run the tool-calling loop with the given provider.

        Args:
            provider: An LLM provider instance (must have chat_with_tools method).
            messages: Initial conversation messages (mutated in-place).
            tool_names: Optional list of tool names to expose to the LLM.
            tool_categories: Optional list of tool categories to expose.
            task_type: Task type for model selection.

        Returns:
            ChatResponse with the final text response.
        """
        tool_definitions: list[ToolDefinition] | None = None

        for _ in range(self.max_iterations):
            # Lazy-load tool definitions on first iteration
            if tool_definitions is None:
                tool_definitions = self.adapter.get_tool_definitions(
                    available_only=True,
                    names=tool_names,
                    categories=tool_categories,
                )
                if not tool_definitions:
                    tool_definitions = None  # no tools available

            try:
                response: ChatResponse = await provider.chat_with_tools(
                    messages,
                    tools=tool_definitions,
                    task_type=task_type,
                )
            except NotImplementedError:
                # Provider doesn't support tool calling — fall back to regular chat
                text = await provider.chat(messages, task_type=task_type)
                return ChatResponse(text=text, tool_calls=None)

            # If no tool calls, the LLM is done
            if not response.tool_calls:
                return response

            # Execute all tool calls concurrently (gated by semaphore).
            # return_exceptions=True ensures one failing tool does not cancel
            # or discard results from other parallel tool calls.
            raw_results: list[ToolCallResult | BaseException] = await asyncio.gather(
                *(
                    self._execute_with_semaphore(self.adapter, tc)
                    for tc in response.tool_calls
                ),
                return_exceptions=True,
            )

            # Convert exceptions to failed ToolCallResult entries so the LLM
            # sees every tool outcome and never loses successful results.
            results: list[ToolCallResult] = []
            for tc, raw in zip(response.tool_calls, raw_results, strict=True):
                if isinstance(raw, BaseException):
                    logger.warning(
                        "Tool call %r (id=%s) failed: %s",
                        tc.name,
                        tc.id,
                        raw,
                    )
                    results.append(
                        ToolCallResult(
                            tool_call_id=tc.id,
                            name=tc.name,
                            result=None,
                            error=str(raw),
                        )
                    )
                else:
                    results.append(raw)

            # Build step messages atomically — only extend messages after gather succeeds
            step_messages: list[ChatMessage] = []

            assistant_msg = ChatMessage(
                role="assistant",
                content=response.text or "",
                tool_calls=[
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": _dict_to_json(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ],
            )
            step_messages.append(assistant_msg)

            # Append tool result messages
            for result in results:
                tool_msg = ChatMessage(
                    role="tool",
                    content=result.format_for_llm(),
                    tool_call_id=result.tool_call_id,
                )
                step_messages.append(tool_msg)

            # Atomically extend messages — no partial state on cancellation
            messages.extend(step_messages)

        logger.warning(
            "Tool-calling loop reached max_iterations=%d — returning last text",
            self.max_iterations,
        )
        return ChatResponse(
            text="Maximum tool-calling iterations reached.",
            tool_calls=None,
        )


def _dict_to_json(d: dict[str, Any]) -> str:
    """Serialize a dict to JSON string for tool call arguments.

    Falls back to ``repr()`` for non-serializable values to avoid
    crashing the tool-calling loop on unexpected types.
    """
    import json

    try:
        return json.dumps(d, ensure_ascii=False, default=repr)
    except (TypeError, ValueError) as exc:
        logger.warning("Failed to serialize tool call arguments: %s", exc)
        return json.dumps({"error": f"serialization failed: {exc}"}, ensure_ascii=False)
