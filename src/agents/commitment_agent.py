"""Commitment Agent — извлекает обещания, дедлайны, договорённости из переписки."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.llm.base import ChatMessage

logger = logging.getLogger(__name__)

COMMITMENT_SYSTEM = """Ты — AI-ассистент. Извлеки обещания и дедлайны из переписки.
Искать: явные обещания («сделаю», «пришлю», «договорились»), дедлайны, обязательства.

Верни JSON: {
  "commitments": [
    {"text": "обещание", "direction": "mine|theirs",
     "deadline": "ISO-дата|null", "contact_name": "имя|null"}
  ]
}
"""


async def extract(provider, messages_text: str) -> dict[str, Any]:
    """Извлекает обязательства из текста переписки.

    Args:
        provider: Объект LLMProvider с методом chat().
        messages_text: Текст переписки для анализа.

    Returns:
        Словарь с ключом commitments (список обещаний).
    """
    if not messages_text.strip():
        return {"commitments": []}

    user_msg = f"Переписка:\n{messages_text[:3000]}"

    try:
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=COMMITMENT_SYSTEM),
                ChatMessage(role="user", content=user_msg),
            ],
            heavy=False,
        )
    except Exception as e:
        logger.error("Commitment agent LLM error: %s", e)
        return {"commitments": []}
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json|JSON)?\s*\n?", "", raw)
        raw = re.sub(r"\n?\s*```\s*$", "", raw)

    try:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            return json.loads(m.group(0))
        return {"commitments": []}
    except Exception:
        logger.debug("Commitment parse failed: %s", raw[:100])
        return {"commitments": []}
