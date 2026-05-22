"""Unified safe wrapper for LLM calls — single error handling for all providers."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Structured result of any LLM call."""

    ok: bool = True
    text: str = ""
    error: str = ""
    error_type: str = (
        ""  # "exhausted" | "timeout" | "context" | "rate" | "generic" | "parse"
    )
    raw_text: str = ""  # original text before JSON parse
    json_data: dict | None = None  # parsed JSON if parse_json=True
    metadata: dict = field(default_factory=dict)


async def safe_llm_call(
    provider: Any,
    messages: list[dict[str, str]],
    *,
    heavy: bool = True,
    parse_json: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float = 120.0,
) -> LLMResponse:
    """Single entry point for ALL LLM calls in Maestro and agents.

    Handles:
      - ExhaustedError → error_type="exhausted"
      - asyncio.TimeoutError → error_type="timeout"
      - "context_length" / "token" in message → error_type="context"
      - "rate" in message → error_type="rate"
      - everything else → error_type="generic"

    If parse_json=True:
      - Strips markdown code fences (```json ... ```)
      - Parses JSON from response
      - Sets response.json_data if successful
      - On parse failure: ok=False, error_type="parse", errors are in error

    Returns LLMResponse — caller checks .ok before using .text or .json_data.
    """
    from src.llm.router import ExhaustedError

    # NOTE: temperature / max_tokens are accepted but NOT forwarded to
    # provider.chat() because LLMProvider protocol only accepts messages + heavy.
    # They are stored in response.metadata for future use when the protocol grows.
    try:
        raw = await asyncio.wait_for(
            provider.chat(messages, heavy=heavy),
            timeout=timeout,
        )
    except ExhaustedError:
        logger.warning("LLM call exhausted (all keys in cooldown)")
        return LLMResponse(
            ok=False,
            error="Все API-ключи исчерпаны",
            error_type="exhausted",
        )
    except asyncio.TimeoutError:
        logger.warning("LLM call timed out after %.0fs", timeout)
        return LLMResponse(
            ok=False,
            error="Таймаут",
            error_type="timeout",
        )
    except Exception as e:
        msg = str(e).lower()
        if "context_length" in msg or "token" in msg:
            logger.warning("LLM context overflow: %s", e)
            return LLMResponse(
                ok=False,
                error=str(e),
                error_type="context",
            )
        if "rate" in msg:
            logger.warning("LLM rate limit: %s", e)
            return LLMResponse(
                ok=False,
                error=str(e),
                error_type="rate",
            )
        logger.exception("LLM call failed with unexpected error")
        return LLMResponse(
            ok=False,
            error=str(e),
            error_type="generic",
        )

    raw = raw.strip()
    response = LLMResponse(
        ok=True,
        text=raw,
        raw_text=raw,
        metadata={
            "heavy": heavy,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
    )

    if parse_json:
        json_text = raw
        if json_text.startswith("```"):
            json_text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", json_text)
            json_text = re.sub(r"\n?\s*```\s*$", "", json_text)
        try:
            response.json_data = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.warning("LLM JSON parse failed: %s", e)
            response.ok = False
            response.error = str(e)
            response.error_type = "parse"

    return response


def response_as_error_text(resp: LLMResponse) -> str:
    """Convert LLMResponse error to user-friendly Russian text.

    Used for returning errors through Telegram bot.
    """
    messages: dict[str, str] = {
        "exhausted": "⚠️ Все API-ключи исчерпаны. Попробуй позже.",
        "timeout": "⏳ LLM не ответил вовремя. Попробуй ещё раз.",
        "context": "📏 Запрос слишком длинный. Сократи текст и попробуй снова.",
        "rate": "🚦 Слишком много запросов. Подожди минуту.",
        "parse": "⚠️ Не удалось разобрать ответ. Попробуй ещё раз.",
        "generic": "❌ Ошибка LLM. Попробуй позже.",
    }
    return messages.get(resp.error_type, messages["generic"])


async def fast_llm_json(
    provider: Any,
    messages: list[dict[str, str]],
    *,
    heavy: bool = False,
    timeout: float = 90.0,
) -> LLMResponse:
    """Wrapper: call LLM and parse JSON in one shot."""
    return await safe_llm_call(
        provider, messages, heavy=heavy, parse_json=True, timeout=timeout
    )
