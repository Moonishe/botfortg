"""Search Agent — находит контакты/чаты по нечёткому запросу."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.agents._json_utils import extract_json_from_llm_response
from src.llm.base import ChatMessage

logger = logging.getLogger(__name__)

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


async def resolve(provider, query: str, contacts: list[dict]) -> dict[str, Any]:
    """Резолвит контакт по нечёткому запросу.

    Args:
        provider: Объект LLMProvider с методом chat().
        query: Поисковый запрос (имя, ник, роль).
        contacts: Список контактов вида
                  [{"display_name": "...", "peer_id": 123, "username": "..."}, ...].

    Returns:
        Словарь с полями found, display_name, peer_id, confidence, reason.
    """
    contacts_preview = contacts[:50]
    contacts_json = json.dumps(contacts_preview, ensure_ascii=False)

    user_msg = f"Запрос: {query}\n\nСписок контактов:\n{contacts_json}"

    try:
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=SEARCH_SYSTEM),
                ChatMessage(role="user", content=user_msg),
            ],
            heavy=False,
        )
    except Exception as e:
        logger.error("Search agent LLM error: %s", e, exc_info=True)
        return {"found": False}
    raw = raw.strip()
    try:
        return extract_json_from_llm_response(raw, default={"found": False})
    except Exception:
        logger.debug("Search parse failed: %s", raw[:100])
        return {"found": False}
