"""Summarizer Agent — саммаризация переписок, catchup, где остановились."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agents._json_utils import extract_json_from_llm_response
from src.config import settings
from src.llm.base import ChatMessage, LLMProvider

logger = logging.getLogger(__name__)

_AGENT_TIMEOUT = 60.0  # seconds — agents are background tasks, 60s is generous

SUMMARY_SYSTEM = """Ты — AI-ассистент. Сделай краткую сводку переписки (5-7 строк).
Только ключевое: договорённости, темы, эмоциональный фон.
Без HTML-тегов, простой текст с эмодзи. На русском.

Верни JSON: {"summary": "текст саммари"}
"""


async def summarize(
    provider: LLMProvider, messages_text: str, *, max_tokens: int | None = None
) -> dict[str, Any]:
    """Саммаризирует переписку."""
    if not messages_text.strip():
        return {"summary": "Нет сообщений."}

    user_msg = f"Переписка:\n{messages_text[:4000]}"

    effective_max_tokens = (
        max_tokens if max_tokens is not None else settings.agent_token_budget
    )

    try:
        from src.llm.retry import retry_async

        raw = await asyncio.wait_for(
            retry_async(
                provider.chat,
                [
                    ChatMessage(role="system", content=SUMMARY_SYSTEM),
                    ChatMessage(role="user", content=user_msg),
                ],
                heavy=False,
                max_tokens=effective_max_tokens,  # type: ignore
            ),
            timeout=_AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("summarizer_agent: LLM call timed out after %ds", _AGENT_TIMEOUT)
        return {"summary": "Не удалось сделать саммари."}
    except Exception as e:
        logger.error("Summarizer agent LLM error: %s", e)
        return {"summary": "Не удалось сделать саммари."}
    try:
        parsed = extract_json_from_llm_response(raw, default={"summary": raw})
        return parsed if isinstance(parsed, dict) else {"summary": raw}
    except Exception:
        logger.warning("summarizer_agent: JSON parse failed")
        return {"summary": "Не удалось сделать саммари."}
