"""Digest Agent — собирает дайджест входящих сообщений."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.agents._json_utils import extract_json_from_llm_response
from src.config import settings
from src.llm.base import ChatMessage, LLMProvider

logger = logging.getLogger(__name__)

_AGENT_TIMEOUT = 60.0  # seconds — agents are background tasks, 60s is generous

DIGEST_SYSTEM = """Ты — AI-ассистент. Собери сводку входящих сообщений.
Дан список с пометками срочности (🔴 urgent, 🟡 important, 🟢 normal). Сгруппируй кратко.

Верни JSON: {
  "urgent_count": N, "important_count": N, "normal_count": N,
  "highlights": ["описание urgent/important"],
  "summary": "общая сводка (2-3 предложения)",
  "html": "HTML с тегами b, i, emoji"
}
"""


async def build_digest(
    provider: LLMProvider, messages_data: list[dict], *, max_tokens: int | None = None
) -> dict[str, Any]:
    """
    Собирает дайджест входящих сообщений.

    Args:
        provider: Объект LLMProvider с методом chat().
        messages_data: Список словарей с ключами:
            sender (str), text (str), urgency (str: urgent/important/normal), count (int).
        max_tokens: Максимальное количество токенов для ответа LLM.
                    Если None, используется settings.agent_token_budget.

    Returns:
        Словарь: urgent_count, important_count, normal_count, highlights, summary, html.
    """
    if not messages_data:
        return {
            "urgent_count": 0,
            "important_count": 0,
            "normal_count": 0,
            "highlights": [],
            "summary": "Нет новых сообщений.",
            "html": "Нет новых сообщений.",
        }

    msgs_json = json.dumps(messages_data, ensure_ascii=False)
    user_msg = f"Входящие сообщения:\n{msgs_json}"

    effective_max_tokens = (
        max_tokens if max_tokens is not None else settings.agent_token_budget
    )

    try:
        raw = await asyncio.wait_for(
            provider.chat(
                [
                    ChatMessage(role="system", content=DIGEST_SYSTEM),
                    ChatMessage(role="user", content=user_msg),
                ],
                heavy=False,
                max_tokens=effective_max_tokens,
            ),
            timeout=_AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("digest_agent: LLM call timed out after %ds", _AGENT_TIMEOUT)
        # ponytail: fallback can't classify urgency/importance without LLM;
        # upgrade path: keep last-known counts or use a heuristic classifier.
        return {
            "urgent_count": 0,
            "important_count": 0,
            "normal_count": len(messages_data),
            "highlights": [],
            "summary": "Ошибка дайджеста.",
            "html": "Ошибка.",
        }
    except Exception as e:
        logger.error("Digest agent LLM error: %s", e)
        # ponytail: fallback can't classify urgency/importance without LLM;
        # upgrade path: keep last-known counts or use a heuristic classifier.
        return {
            "urgent_count": 0,
            "important_count": 0,
            "normal_count": len(messages_data),
            "highlights": [],
            "summary": "Ошибка дайджеста.",
            "html": "Ошибка.",
        }
    try:
        parsed = extract_json_from_llm_response(raw)
        if isinstance(parsed, dict):
            return parsed
        return {
            "urgent_count": len(messages_data),
            "important_count": 0,
            "normal_count": 0,
            "highlights": [],
            "summary": raw,
            "html": raw,
        }
    except Exception:
        logger.warning("Digest parse failed: %s", raw[:100])
        # ponytail: same fallback limitation as LLM error path above.
        return {
            "urgent_count": 0,
            "important_count": 0,
            "normal_count": len(messages_data),
            "highlights": [],
            "summary": "Ошибка построения дайджеста.",
            "html": "Ошибка.",
        }
