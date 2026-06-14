"""MCP tool: mcp_delegate — spawn isolated subagent for parallel work.

Inspired by Hermes Agent's delegate_task.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)

_MAX_DELEGATE_STEPS = 5


@tool(
    name="mcp_delegate",
    description="Запустить под-агента с изолированным контекстом для параллельной задачи",
    category="agent",
    risk="medium",
    params={
        "task": "str — описание задачи для под-агента",
        "max_steps": "int (default 5) — максимум шагов",
        "provider": "str (опционально) — провайдер для под-агента",
    },
)
async def mcp_delegate(
    task: str,
    max_steps: int = _MAX_DELEGATE_STEPS,
    provider: str = "",
) -> dict[str, Any]:
    if not task.strip():
        return {"error": "task is required"}

    steps = min(max(1, max_steps), 10)

    try:
        from src.llm.router import build_provider
        from src.db.session import get_session
        from src.db.repo import get_or_create_user
        from src.config import settings

        async with get_session() as session:
            owner = await get_or_create_user(session, settings.owner_telegram_id)
            if provider:
                llm = await build_provider(session, owner, purpose="background")
            else:
                llm = await build_provider(session, owner, purpose="main")

        from src.llm.base import ChatMessage

        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a specialized sub-agent. Complete the assigned task "
                    "using the available tools. Be concise. "
                    f"Maximum {steps} tool calls allowed."
                ),
            ),
            ChatMessage(role="user", content=task),
        ]

        from src.core.actions.tool_registry import tool_registry

        results: list[str] = []
        for step in range(steps):
            try:
                response = await llm.chat(messages)
                messages.append(ChatMessage(role="assistant", content=response))
                results.append(response)

                if "DONE" in response.upper() or "COMPLETE" in response.upper():
                    break
            except Exception as e:
                results.append(f"[step {step + 1} error: {e}]")
                break

        return {
            "ok": True,
            "steps_used": len(results),
            "result": results[-1][:2000] if results else "(empty)",
            "all_steps": results,
        }

    except Exception as e:
        logger.exception("mcp_delegate failed")
        return {"error": str(e)}
