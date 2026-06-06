"""Digest Agent — собирает дайджест входящих сообщений."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.llm.base import ChatMessage

logger = logging.getLogger(__name__)

DIGEST_SYSTEM = """Ты — AI-ассистент. Собери сводку входящих сообщений.
Дан список с пометками срочности (🔴 urgent, 🟡 important, 🟢 normal). Сгруппируй кратко.

Верни JSON: {
  "urgent_count": N, "important_count": N, "normal_count": N,
  "highlights": ["описание urgent/important"],
  "summary": "общая сводка (2-3 предложения)",
  "html": "HTML с тегами b, i, emoji"
}
"""


async def build_digest(provider, messages_data: list[dict]) -> dict[str, Any]:
    """
    Собирает дайджест входящих сообщений.

    Args:
        provider: Объект LLMProvider с методом chat().
        messages_data: Список словарей с ключами:
            sender (str), text (str), urgency (str: urgent/important/normal), count (int).

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

    try:
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=DIGEST_SYSTEM),
                ChatMessage(role="user", content=user_msg),
            ],
            heavy=False,
        )
    except Exception as e:
        logger.error("Digest agent LLM error: %s", e)
        return {
            "urgent_count": 0,
            "important_count": 0,
            "normal_count": len(messages_data),
            "highlights": [],
            "summary": "Ошибка дайджеста.",
            "html": "Ошибка.",
        }
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json|JSON)?\s*\n?", "", raw)
        raw = re.sub(r"\n?\s*```\s*$", "", raw)

    try:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            return json.loads(m.group(0))
        return {
            "urgent_count": len(messages_data),
            "important_count": 0,
            "normal_count": 0,
            "highlights": [],
            "summary": raw,
            "html": raw,
        }
    except Exception:
        logger.debug("Digest parse failed: %s", raw[:100])
        return {
            "urgent_count": 0,
            "important_count": 0,
            "normal_count": len(messages_data),
            "highlights": [],
            "summary": "Ошибка построения дайджеста.",
            "html": "Ошибка.",
        }
