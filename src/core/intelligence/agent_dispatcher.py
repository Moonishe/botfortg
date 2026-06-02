"""Agent dispatcher — agent execution engine extracted from maestro.py.

Break circular dependency: maestro ↔ agent_orchestrator.
Contains: AGENT_REGISTRY, invokers, _execute_agent, _execute_agents_parallel,
and formatting helpers.  agent_orchestrator imports _execute_agent from here
instead of maestro, breaking the cycle.
"""

from __future__ import annotations
import asyncio
import importlib
import logging
from typing import Any

from src.core.infra.key_guard import safe_str
from src.db.repo import (
    fetch_my_messages_global,
    get_or_create_user,
    list_contacts,
    search_memories,
)
from src.db.session import get_session
from src.llm.base import ChatMessage, TaskType

logger = logging.getLogger(__name__)


# ---- Agent dispatch table ----

AGENT_REGISTRY: dict[str, tuple[str, str]] = {
    "search": ("src.agents.search_agent", "resolve"),
    "memory": ("src.agents.memory_agent", "recall"),
    "urgency": ("src.core.contacts.urgency_classifier", "classify_message"),
    "commitment": ("src.agents.commitment_agent", "extract"),
    "summarizer": ("src.agents.summarizer_agent", "summarize"),
    "draft": ("src.agents.draft_agent", "draft"),
    "digest": ("src.agents.digest_agent", "build_digest"),
    "skill_creator": ("src.agents.skill_creator_agent", "propose"),
}


async def _invoke_search(func, provider, query, owner_id, **kwargs):
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        contacts = await list_contacts(session, owner)
        contact_dicts = [
            {"id": c.peer_id, "name": c.display_name, "username": c.username}
            for c in contacts[:50]
        ]
    data = await func(provider, query, contact_dicts)
    return {"data": data, "success": True}


async def _invoke_memory(func, provider, query, owner_id, **kwargs):
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        facts_obj = await search_memories(session, owner, query)
        facts_list = [m.fact for m in facts_obj] if facts_obj else []
    data = await func(provider, query, facts_list)
    return {"data": data, "success": True}


async def _invoke_urgency(func, _provider, query, _owner_id, **kwargs):
    urgency = func(query)
    return {"data": {"urgency": urgency}, "success": True}


async def _invoke_commitment(func, provider, query, _owner_id, **kwargs):
    data = await func(provider, query)
    return {"data": data, "success": True}


async def _invoke_summarizer(func, provider, query, _owner_id, **kwargs):
    data = await func(provider, query)
    return {"data": data, "success": True}


async def _invoke_draft(func, provider, query, _owner_id, **kwargs):
    agent_spec = kwargs.get("agent_spec", {})
    contact_name = (
        agent_spec.get("contact_name") or agent_spec.get("sender_name") or "собеседник"
    )
    data = await func(provider, contact_name, query)
    return {"data": data, "success": True}


async def _invoke_digest(func, provider, query, _owner_id, **kwargs):
    data = await func(provider, [{"text": query}])
    return {"data": data, "success": True}


async def _invoke_skill_creator(func, provider, query, owner_id, **kwargs):
    """Вызывает skill_creator агент: собирает последние сообщения и анализирует."""
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        messages_raw = await fetch_my_messages_global(session, owner, limit=50)
        recent_messages = [
            {
                "text": msg.text or "",
                "is_outgoing": msg.is_outgoing if hasattr(msg, "is_outgoing") else True,
                "timestamp": str(msg.date) if hasattr(msg, "date") else "",
            }
            for msg in messages_raw
        ]
    data = await func(provider, recent_messages)
    return {"data": data, "success": True}


async def _invoke_delegate(
    func: Any, provider, query: str, owner_id: int, **kwargs: Any
) -> dict:
    """Invokes a dynamic sub-agent with its own LLM call.

    The sub-agent receives a task description, optional instructions,
    and context, then runs an independent LLM analysis.

    Agent spec fields used:
        task (str): what to analyse (overrides query)
        instructions (str|None): custom system prompt additions
        context (str|None): additional data to analyse
        contact (str|None): optional contact to scope analysis
    """
    agent_spec = kwargs.get("agent_spec", {})
    task = agent_spec.get("task", query)
    instructions = agent_spec.get("instructions", "")
    context_data = agent_spec.get("context", "")

    system = (
        "Ты — аналитический суб-агент. Выполни анализ задачи и верни "
        "структурированный ответ. Будь точен, аргументирован и лаконичен."
    )
    if instructions:
        system += f"\n\nДополнительные инструкции:\n{instructions}"

    user_prompt = f"Задача: {task}"
    if context_data:
        user_prompt += f"\n\nКонтекст:\n{context_data}"

    try:
        raw = await asyncio.wait_for(
            provider.chat(
                [
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user_prompt),
                ],
                task_type=TaskType.DEFAULT,
            ),
            timeout=60.0,
        )
        return {
            "data": {"analysis": raw.strip(), "task": task},
            "success": True,
        }
    except Exception:
        logger.exception("delegate agent failed: %s", task)
        return {
            "data": {},
            "success": False,
            "error": "Sub-agent analysis failed",
        }


async def _invoke_random(
    func: Any, provider, query: str, owner_id: int, **kwargs: Any
) -> dict:
    """Random agent — handles non-standard/creative tasks with minimal prompting.

    Uses the default provider to respond to queries without domain-specific
    instructions. Suitable for creative, out-of-the-box, or unusual requests.
    """
    system = (
        "Ты — агент для нестандартных и творческих задач. "
        "Отвечай креативно, нестандартно, с юмором. "
        "Будь живым собеседником."
    )
    try:
        raw = await asyncio.wait_for(
            provider.chat(
                [
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=query),
                ],
                task_type=TaskType.DEFAULT,
            ),
            timeout=60.0,
        )
        return {
            "data": {"response": raw.strip(), "query": query},
            "success": True,
        }
    except Exception:
        logger.exception("random agent failed: %s", query)
        return {
            "data": {},
            "success": False,
            "error": "Random agent failed",
        }


_AGENT_INVOKERS: dict[str, Any] = {
    "search": _invoke_search,
    "memory": _invoke_memory,
    "urgency": _invoke_urgency,
    "commitment": _invoke_commitment,
    "summarizer": _invoke_summarizer,
    "draft": _invoke_draft,
    "digest": _invoke_digest,
    "skill_creator": _invoke_skill_creator,
    "delegate": _invoke_delegate,
    "random": _invoke_random,
}


def _agent_result_as_text(agent_type: str, result: dict) -> str:
    """Форматирует результат агента для вставки в промпт."""
    if not result.get("success", True):
        err = result.get("error", "неизвестная ошибка")
        return f"[{agent_type}] ❌ Ошибка: {err}"

    data = result.get("data", {})
    if not data:
        return f"[{agent_type}] ✅ Выполнен, но данных нет."

    # Сжимаем большие поля
    lines = [f"[{agent_type}]", "Найдено:"]
    for k, v in data.items():
        s = str(v)
        if len(s) > 400:
            s = s[:400] + "…"
        lines.append(f"  {k}: {s}")
    return "\n".join(lines)


async def _execute_agent(
    provider,
    agent_spec: dict,
    *,
    owner_id: int,
) -> dict:
    """Исполняет одного агента по спецификации из плана maestro."""
    agent_type = agent_spec.get("agent", "")
    query = agent_spec.get("query", "")

    # --- Special case: delegate + random (no AGENT_REGISTRY lookup needed) ---
    if agent_type in {"delegate", "random"}:
        invoker = _AGENT_INVOKERS.get(agent_type)
        if invoker is None:
            logger.error("%s invoker not found", agent_type)
            return {
                "agent": agent_type,
                "data": {},
                "success": False,
                "error": f"{agent_type} invoker not found",
            }
        try:
            result = await invoker(
                None, provider, query, owner_id, agent_spec=agent_spec
            )
            result["agent"] = agent_type
            return result
        except Exception as e:
            logger.exception("%s agent failed", agent_type)
            return {
                "agent": agent_type,
                "data": {},
                "success": False,
                "error": safe_str(e),
            }

    # --- Normal agent lookup ---
    agent_info = AGENT_REGISTRY.get(agent_type)
    if agent_info is None:
        logger.warning("Unknown agent type: %s", agent_type)
        return {
            "agent": agent_type,
            "data": {},
            "success": False,
            "error": "Неизвестный агент: " + agent_type,
        }

    invoker = _AGENT_INVOKERS.get(agent_type)
    if invoker is None:
        logger.error("No invoker registered for agent: %s", agent_type)
        return {
            "agent": agent_type,
            "data": {},
            "success": False,
            "error": "Нет обработчика для агента: " + agent_type,
        }

    module_path, func_name = agent_info
    try:
        module = importlib.import_module(module_path)
        agent_func = getattr(module, func_name)
        result = await invoker(
            agent_func, provider, query, owner_id, agent_spec=agent_spec
        )
        result["agent"] = agent_type
        return result
    except Exception as e:
        logger.exception("Agent %s failed", agent_type)
        return {"agent": agent_type, "data": {}, "success": False, "error": safe_str(e)}


async def _execute_agents_parallel(
    provider, agents_to_call: list, *, owner_id: int
) -> list[dict]:
    """Запускает нескольких агентов параллельно (каждый со своей сессией БД)."""
    if not agents_to_call:
        return []

    tasks = [
        _execute_agent(provider, spec, owner_id=owner_id) for spec in agents_to_call
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[dict] = []
    for i, r in enumerate(raw_results):
        if isinstance(r, Exception):
            agent_name = agents_to_call[i].get("agent", "?")
            logger.error("Agent %s failed with exception: %s", agent_name, r)
            results.append(
                {
                    "agent": agent_name,
                    "data": {},
                    "success": False,
                    "error": str(r),
                }
            )
        else:
            results.append(r)
    return results
