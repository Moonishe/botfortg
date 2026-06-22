"""Commitment Agent — извлекает обещания, дедлайны, договорённости из переписки."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agents._json_utils import extract_json_from_llm_response
from src.config import settings
from src.llm.base import ChatMessage, LLMProvider

logger = logging.getLogger(__name__)

_AGENT_TIMEOUT = 60.0  # seconds — agents are background tasks, 60s is generous

COMMITMENT_SYSTEM = """Ты — AI-ассистент. Извлеки обещания и дедлайны из переписки.
Искать: явные обещания («сделаю», «пришлю», «договорились»), дедлайны, обязательства.

Верни JSON: {
  "commitments": [
    {"text": "обещание", "direction": "mine|theirs",
     "deadline": "ISO-дата|null", "contact_name": "имя|null"}
  ]
}
"""


async def extract(
    provider: LLMProvider, messages_text: str, *, max_tokens: int | None = None
) -> dict[str, Any]:
    """Извлекает обязательства из текста переписки.

    Args:
        provider: Объект LLMProvider с методом chat().
        messages_text: Текст переписки для анализа.
        max_tokens: Максимальное количество токенов для ответа LLM.
                    Если None, используется settings.agent_token_budget.

    Returns:
        Словарь с ключом commitments (список обещаний).
    """
    if not messages_text.strip():
        return {"commitments": []}

    user_msg = f"Переписка:\n{messages_text[:3000]}"

    effective_max_tokens = (
        max_tokens if max_tokens is not None else settings.agent_token_budget
    )

    try:
        raw = await asyncio.wait_for(
            provider.chat(
                [
                    ChatMessage(role="system", content=COMMITMENT_SYSTEM),
                    ChatMessage(role="user", content=user_msg),
                ],
                heavy=False,
                max_tokens=effective_max_tokens,
            ),
            timeout=_AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("commitment_agent: LLM call timed out after %ds", _AGENT_TIMEOUT)
        return {"commitments": []}
    except Exception as e:
        logger.error("Commitment agent LLM error: %s", e)
        return {"commitments": []}
    try:
        parsed = extract_json_from_llm_response(raw, default={"commitments": []})
        return parsed if isinstance(parsed, dict) else {"commitments": []}
    except Exception:
        logger.warning("Commitment parse failed: %s", raw[:100])
        return {"commitments": []}
