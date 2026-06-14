"""MCP tool: mcp_self_model — управление LLM-моделью."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update as sa_update

from src.core.actions.tool_registry import tool
from src.db.models._auth import LlmKeySlot
from src.db.session import get_session

logger = logging.getLogger(__name__)


@tool(
    name="mcp_self_model",
    description="Управление LLM-моделью: текущая, список, переключить",
    category="admin",
    risk="high",
    requires_confirmation=True,
    params={
        "action": "str — current | list_providers | switch | list_models",
        "provider": "str (для switch/list_models)",
        "model": "str (для switch)",
        "slot_id": "int (для list_models)",
    },
)
async def mcp_self_model(
    action: str,
    provider: str = "",
    model: str = "",
    slot_id: int = 0,
) -> dict[str, Any]:
    if action == "current":
        async with get_session() as session:
            result = await session.execute(
                select(LlmKeySlot)
                .where(LlmKeySlot.enabled == True)  # noqa: E712
                .order_by(LlmKeySlot.priority.desc())
                .limit(1)
            )
            slot = result.scalar_one_or_none()
            if slot is None:
                return {"error": "No enabled LLM key slot found"}
            return {
                "provider": slot.provider,
                "model": slot.model or "default",
                "endpoint": slot.endpoint or "default",
                "slot_id": slot.id,
                "purpose": slot.purpose,
                "priority": slot.priority,
            }

    elif action == "list_providers":
        async with get_session() as session:
            result = await session.execute(
                select(
                    LlmKeySlot.provider,
                    LlmKeySlot.id,
                    LlmKeySlot.enabled,
                    LlmKeySlot.priority,
                )
            )
            providers = [
                {"provider": r[0], "slot_id": r[1], "enabled": r[2], "priority": r[3]}
                for r in result
            ]
        return {"providers": providers, "total": len(providers)}

    elif action == "list_models":
        sid = slot_id or 0
        async with get_session() as session:
            result = await session.execute(
                select(LlmKeySlot).where(LlmKeySlot.id == sid)
            )
            slot = result.scalar_one_or_none()
        if slot is None:
            return {"error": f"Slot {sid} not found"}
        model_names = [m.model_name for m in slot.models] if slot.models else []
        return {
            "slot_id": slot.id,
            "provider": slot.provider,
            "default_model": slot.model,
            "models": model_names,
        }

    elif action == "switch":
        if not provider:
            return {"error": "provider is required"}
        async with get_session() as session:
            await session.execute(sa_update(LlmKeySlot).values(priority=0))
            query = select(LlmKeySlot).where(LlmKeySlot.provider == provider)
            if model:
                query = query.where(LlmKeySlot.model == model)
            result = await session.execute(query.order_by(LlmKeySlot.id))
            slot = result.scalar_one_or_none()
            if slot is None:
                return {"error": f"No slot for provider={provider}"}
            slot.priority = 100
            slot.enabled = True
            await session.commit()

            try:
                from src.llm.router import _CIRCUIT_BREAKERS

                _CIRCUIT_BREAKERS.clear()
            except Exception:
                pass

        return {
            "ok": True,
            "provider": provider,
            "model": slot.model or "default",
            "slot_id": slot.id,
        }

    return {"error": f"Unknown action: {action}"}
