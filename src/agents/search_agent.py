"""Search Agent — находит контакты/чаты по нечёткому запросу."""

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

SEARCH_SYSTEM = """Ты — AI-ассистент в Telegram. Найди контакт или чат по запросу.
Дан список контактов (имя, username, телефон). Выбери наиболее подходящий.

Верни ТОЛЬКО JSON:
{
  "found": true|false,
  "display_name": "точное имя",
  "peer_id": 123,
  "confidence": 0.0-1.0,
  "reason": "почему выбран (1 фраза)"
}
Если не нашёл — "found": false. Несколько похожих → самый вероятный, confidence < 0.7.
Учитывай ласкательные формы (Настя=Анастасия) и роли (мама, брат, босс, жена).
"""


async def resolve(
    provider: LLMProvider,
    query: str,
    contacts: list[dict],
    *,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Резолвит контакт по нечёткому запросу.

    Args:
        provider: Объект LLMProvider с методом chat().
        query: Поисковый запрос (имя, ник, роль).
        contacts: Список контактов вида
                  [{"display_name": "...", "peer_id": 123, "username": "..."}, ...].
        max_tokens: Максимальное количество токенов для ответа LLM.
                    Если None, используется settings.agent_token_budget.

    Returns:
        Словарь с полями found, display_name, peer_id, confidence, reason.
    """
    if len(contacts) > 50:
        logger.warning(
            "search_agent: truncated %d contacts to 50 for prompt", len(contacts)
        )
    contacts_preview = contacts[:50]
    contacts_json = json.dumps(contacts_preview, ensure_ascii=False)

    user_msg = f"Запрос: {query}\n\nСписок контактов:\n{contacts_json}"

    effective_max_tokens = (
        max_tokens if max_tokens is not None else settings.agent_token_budget
    )

    try:
        raw = await asyncio.wait_for(
            provider.chat(
                [
                    ChatMessage(role="system", content=SEARCH_SYSTEM),
                    ChatMessage(role="user", content=user_msg),
                ],
                heavy=False,
                max_tokens=effective_max_tokens,
            ),
            timeout=_AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("search_agent: LLM call timed out after %ds", _AGENT_TIMEOUT)
        return {"found": False}
    except Exception as e:
        logger.error("Search agent LLM error: %s", e, exc_info=True)
        return {"found": False}
    raw = raw.strip()
    try:
        return extract_json_from_llm_response(raw, default={"found": False})
    except Exception:
        logger.warning("Search parse failed: %s", raw[:100])
        return {"found": False}
