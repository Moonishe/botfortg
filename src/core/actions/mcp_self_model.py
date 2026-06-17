"""MCP tool: mcp_self_model — управление LLM-моделью."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update as sa_update

from src.db.models._auth import LlmKeySlot
from src.db.session import get_session

logger = logging.getLogger(__name__)


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
        # Read slot.models INSIDE the session — the relationship is lazy and
        # accessing it after the session closes raises DetachedInstanceError /
        # MissingGreenlet. Snapshot the scalar fields too so the return dict
        # never touches the detached ORM object.
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
            # Find the target slot first so we know the user to scope the
            # priority reset to (avoids a cross-user side-effect).
            query = select(LlmKeySlot).where(LlmKeySlot.provider == provider)
            if model:
                query = query.where(LlmKeySlot.model == model)
            result = await session.execute(query.order_by(LlmKeySlot.id))
            slot = result.scalars().first()
            if slot is None:
                return {"error": f"No slot for provider={provider}"}
            # Reset all slots for THIS user to priority 0
            await session.execute(
                sa_update(LlmKeySlot)
                .where(LlmKeySlot.user_id == slot.user_id)
                .values(priority=0)
            )
            slot.priority = 100
            slot.enabled = True
            await session.commit()

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

        return {
            "ok": True,
            "provider": provider,
            "model": slot.model or "default",
            "slot_id": slot.id,
        }

    return {"error": f"Unknown action: {action}"}


# ── Auto-register for MCP exposure ──
from src.core.actions.mcp_expose import expose_to_mcp

expose_to_mcp(
    "mcp_self_model",
    description=(
        "Manage LLM provider/model: current, list_providers, list_models, switch"
    ),
)
