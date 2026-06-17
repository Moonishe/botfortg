"""Bounded session memory snapshot — compact, token-aware context for prompts.

Week 5: replaces the bare 3-fact frozen snapshot with a structured,
bounded snapshot that includes:
- 3-7 relevant facts
- per-contact digest (if a contact is active)
- pending questions
- communication style hints
- risk hints
- session summary
- token estimate
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.core.context.token_tracker import estimate_tokens
from src.core.memory.memory_recall import recall
from src.core.memory.pending_questions import peek_pending
from src.core.security.prompt_injection_scanner import scan_content

logger = logging.getLogger(__name__)

# ponytail: hard ceiling for formatted snapshot text; if exceeded, facts
# are trimmed. Upgrade path: per-model tokenizer via settings.
MAX_SNAPSHOT_TOKENS = 512


def _empty_snapshot() -> dict[str, Any]:
    return {
        "facts": [],
        "contact_digest": None,
        "pending_questions": [],
        "style": "",
        "risk_hints": [],
        "session_summary": "",
        "active_tasks": "",
        "token_estimate": 0,
    }


async def build_session_snapshot(
    telegram_id: int,
    contact_id: int | None = None,
    user_text: str = "",
    *,
    max_facts: int = 7,
) -> dict[str, Any]:
    """Build a bounded session memory snapshot for the prompt.

    Args:
        telegram_id: Telegram user ID of the owner.
        contact_id: Optional active contact peer ID.
        user_text: Current user message (used as recall query).
        max_facts: Upper bound on facts included (default 7).

    Returns:
        Snapshot dict. Never raises; on failure returns an empty snapshot.
    """
    snapshot = _empty_snapshot()

    # 1. Gather independent I/O sources in parallel
    _fact_task = _gather_facts(telegram_id, user_text, max_facts)
    _session_task = _gather_session_context(telegram_id)
    _digest_task = _gather_contact_digest(telegram_id, contact_id)
    _pending_task = _gather_pending_questions(telegram_id)

    try:
        (
            _facts,
            _session_data,
            _digest_data,
            _pending_questions,
        ) = await asyncio.gather(
            _fact_task,
            _session_task,
            _digest_task,
            _pending_task,
            return_exceptions=True,
        )
    except Exception:
        logger.debug("snapshot gather failed", exc_info=True)
        return snapshot

    if not isinstance(_facts, BaseException):
        snapshot["facts"] = _facts
    if not isinstance(_session_data, BaseException):
        snapshot["session_summary"] = _session_data.get("session_summary", "")
        snapshot["active_tasks"] = _session_data.get("active_tasks", "")
    if not isinstance(_digest_data, BaseException):
        snapshot["contact_digest"] = _digest_data["digest"]
        snapshot["style"] = _digest_data["style"]
        snapshot["risk_hints"] = _digest_data["risk_hints"]
    if not isinstance(_pending_questions, BaseException):
        snapshot["pending_questions"] = _pending_questions

    # 2. Trim facts if snapshot exceeds token budget
    _trim_facts_to_budget(snapshot)

    # 3. Token estimate (format once, reuse for estimate)
    _formatted = format_snapshot(snapshot)
    snapshot["token_estimate"] = estimate_tokens(_formatted)

    return snapshot


async def _gather_facts(telegram_id: int, user_text: str, max_facts: int) -> list[str]:
    try:
        _recall_result = await recall(
            telegram_id=telegram_id,
            query=user_text,
            limit=max_facts,
            include_deep=False,
            mode="normal",
        )
        return [f.fact for f in _recall_result.facts[:max_facts]]
    except Exception:
        logger.debug("recall failed in session snapshot", exc_info=True)
        return []


async def _gather_session_context(telegram_id: int) -> dict[str, Any]:
    try:
        from src.core.memory.session_context import load_session_context

        _ctx = await load_session_context(telegram_id)
        if _ctx:
            return {
                "session_summary": _ctx.get("context_summary") or "",
                "active_tasks": _ctx.get("active_tasks") or "",
            }
    except Exception:
        logger.debug("session_context load failed in snapshot", exc_info=True)
    return {"session_summary": "", "active_tasks": ""}


async def _gather_contact_digest(
    telegram_id: int, contact_id: int | None
) -> dict[str, Any]:
    out: dict[str, Any] = {"digest": None, "style": "", "risk_hints": []}
    if not contact_id or contact_id <= 0:
        return out
    try:
        from src.core.contacts.contact_memory_digest import get_contact_digest

        _digest = await get_contact_digest(telegram_id, contact_id)
        out["digest"] = _digest
        _style = _digest.get("style") or {}
        if _style:
            out["style"] = (
                f"closeness={_style.get('closeness')}, "
                f"archetype={_style.get('archetype')}, "
                f"directness={_style.get('directness')}, "
                f"tone={_style.get('tone')}"
            )
        _risks = _digest.get("risks") or []
        out["risk_hints"] = [
            r.get("type") if isinstance(r, dict) else str(r) for r in _risks
        ]
    except Exception:
        logger.debug("contact digest failed in snapshot", exc_info=True)
    return out


async def _gather_pending_questions(telegram_id: int) -> list[str]:
    try:
        _pending = await peek_pending(telegram_id)
        return [q.get("question", "") for q in _pending[-5:] if q.get("question")]
    except Exception:
        logger.debug("pending questions peek failed in snapshot", exc_info=True)
        return []


def _trim_facts_to_budget(snapshot: dict[str, Any]) -> None:
    """Reduce facts until the formatted snapshot fits the token budget.

    Keeps the snapshot bounded even if recall returns many long facts.
    Stops when one fact remains to avoid an empty snapshot.
    """
    facts = snapshot.get("facts") or []
    if not facts:
        return

    while len(facts) > 1:
        _text = format_snapshot(snapshot)
        if estimate_tokens(_text) <= MAX_SNAPSHOT_TOKENS:
            break
        # Drop the last fact first (usually lowest relevance)
        facts.pop()
    snapshot["facts"] = facts


def format_snapshot(snapshot: dict[str, Any] | None) -> str:
    """Format snapshot as a compact prompt block.

    Args:
        snapshot: Snapshot dict from build_session_snapshot() or None.

    Returns:
        Compact text block for injection into the system prompt.
        Returns empty string if the content is blocked by the injection scanner.
    """
    if not snapshot:
        return ""

    parts: list[str] = []

    _session_summary = snapshot.get("session_summary") or ""
    if _session_summary:
        parts.append(f"[КОНТЕКСТ СЕССИИ]\n{_session_summary}")

    _facts = snapshot.get("facts") or []
    if _facts:
        parts.append(
            "[ПАМЯТЬ] Используй факты естественно, не перечисляй списком:\n"
            + "\n".join(f"- {f}" for f in _facts)
        )

    _digest = snapshot.get("contact_digest")
    if _digest:
        _name = _digest.get("display_name") or "?"
        _contact_line = f"Контакт: {_name}"
        if snapshot.get("style"):
            _contact_line += f" | стиль: {snapshot['style']}"
        parts.append(_contact_line)

        _promises = _digest.get("promises") or []
        if _promises:
            _promises_text = ", ".join(p.get("text", "") for p in _promises)
            parts.append(f"Обещания: {_promises_text}")

        _risks = snapshot.get("risk_hints") or []
        if _risks:
            parts.append(f"Риски: {', '.join(str(r) for r in _risks)}")

    _pending = snapshot.get("pending_questions") or []
    if _pending:
        parts.append(f"[ОЖИДАЮТ ОТВЕТА] {'; '.join(_pending)}")

    text = "\n\n".join(parts)
    scan = scan_content(text, filename="session_snapshot")
    if scan.blocked:
        logger.warning(
            "Session snapshot blocked by prompt injection scanner: %s", scan.message
        )
        return ""
    return text
