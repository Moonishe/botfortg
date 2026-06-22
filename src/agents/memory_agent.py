"""Memory Agent — извлекает и хранит факты о контактах."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agents._json_utils import extract_json_from_llm_response
from src.config import settings
from src.llm.base import ChatMessage, LLMProvider

logger = logging.getLogger(__name__)

_AGENT_TIMEOUT = 60.0  # seconds — agents are background tasks, 60s is generous

RECALL_SYSTEM = """Ты — AI-ассистент. Ответь на вопрос о контакте, используя ТОЛЬКО сохранённые факты.

Верни JSON: {"answer": "ответ на основе фактов", "relevant_facts": ["факт1", "факт2"]}
Если фактов недостаточно — "answer": "недостаточно данных".
"""


async def recall(
    provider: LLMProvider,
    query: str,
    facts: list[str],
    *,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Отвечает на вопрос о контакте на основе сохранённых фактов.

    Args:
        provider: Объект LLMProvider с методом chat().
        query: Вопрос пользователя о контакте.
        facts: Список сохранённых фактов (строки).
        max_tokens: Максимальное количество токенов для ответа LLM.
                    Если None, используется settings.agent_token_budget.

    Returns:
        Словарь с ключами answer (str) и relevant_facts (list[str]).
    """
    if not facts:
        return {"answer": "Нет сохранённых фактов.", "relevant_facts": []}

    facts_str = "\n".join(f"- {f}" for f in facts[:20])
    user_msg = f"Факты о контакте:\n{facts_str}\n\nВопрос: {query}"

    effective_max_tokens = (
        max_tokens if max_tokens is not None else settings.agent_token_budget
    )

    try:
        from src.llm.retry import retry_async

        raw = await asyncio.wait_for(
            retry_async(
                provider.chat,
                [
                    ChatMessage(role="system", content=RECALL_SYSTEM),
                    ChatMessage(role="user", content=user_msg),
                ],
                heavy=False,
                max_tokens=effective_max_tokens,  # type: ignore
            ),
            timeout=_AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("memory_agent: LLM call timed out after %ds", _AGENT_TIMEOUT)
        return {"answer": "Не удалось проанализировать.", "relevant_facts": []}
    except Exception as e:
        logger.error("Memory agent LLM error: %s", e)
        return {"answer": "Не удалось проанализировать.", "relevant_facts": []}
    try:
        parsed = extract_json_from_llm_response(
            raw, default={"answer": raw, "relevant_facts": []}
        )
        return (
            parsed
            if isinstance(parsed, dict)
            else {"answer": raw, "relevant_facts": []}
        )
    except Exception:
        logger.warning("memory_agent: JSON parse failed")
        return {"answer": "Не удалось проанализировать.", "relevant_facts": []}
