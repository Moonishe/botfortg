"""Skill Creator Agent — анализирует историю сообщений и предлагает новые навыки."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agents._json_utils import extract_json_from_llm_response
from src.config import settings
from src.llm.base import ChatMessage, LLMProvider

logger = logging.getLogger(__name__)

_AGENT_TIMEOUT = 60.0  # seconds — agents are background tasks, 60s is generous

SKILL_CREATOR_SYSTEM = """Ты — агент-аналитик AI-ассистента в Telegram. 
Анализируй паттерны общения пользователя и предлагай новые навыки (skills) для ассистента.

## Что такое навык (skill)
Навык — это компактная многократно используемая процедура, которая внедряется в промпт,
когда её триггерные паттерны совпадают с текущим запросом. Навык помогает ассистенту
выполнять типовые задачи быстрее и точнее, не обращаясь к тяжёлой LLM-модели.

## Задача
Проанализируй историю сообщений пользователя и предложи до 5 новых навыков, которые:
1. Покрывают повторяющиеся паттерны запросов
2. Автоматизируют рутинные действия
3. Улучшают качество ответов в конкретных сценариях
4. Заполняют пробелы в текущих возможностях ассистента

## Правила
- Навык должен быть конкретным и actionable
- trigger_patterns — это список регулярных выражений или ключевых слов (на русском)
- body — конкретная процедура (что делать, в каком порядке)
- confidence — твоя уверенность (0.0-1.0), что навык действительно полезен
- Не предлагай навыки, дублирующие базовые функции (поиск, напоминания, отправка сообщений)
- Учитывай контекст: о чём пользователь спрашивает чаще всего
- Если пользователь часто просит "найди", "расскажи", "подведи итог" — это уже есть, не дублируй

## Формат ответа
Верни ТОЛЬКО JSON-массив:
[
  {
    "name": "краткое_имя_навыка",
    "trigger_patterns": ["паттерн1", "паттерн2"],
    "description": "Что делает навык (1-2 предложения)",
    "body": "Конкретная пошаговая процедура выполнения. Что проверить, как ответить, какие данные собрать.",
    "confidence": 0.85
  }
]

Если полезных навыков не найдено — верни пустой массив [].
"""


async def propose(
    provider: LLMProvider,
    recent_messages: list[dict[str, Any]],
    *,
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """Анализирует историю сообщений и предлагает новые навыки.

    Args:
        provider: Объект LLMProvider с методом chat().
        recent_messages: Список сообщений вида
            [{"text": "...", "is_outgoing": bool, "timestamp": "..."}, ...].
        max_tokens: Максимальное количество токенов для ответа LLM.
                    Если None, используется settings.agent_token_budget.

    Returns:
        Список предложенных навыков:
        [{"name": "...", "trigger_patterns": [...], "description": "...",
          "body": "...", "confidence": 0.8}, ...]
    """
    if not recent_messages:
        logger.debug("Skill creator: no messages to analyze")
        return []

    # Формируем компактную сводку сообщений (последние 200 сообщений макс)
    sample = recent_messages[-200:]
    transcript_parts: list[str] = []
    for msg in sample:
        direction = "→" if msg.get("is_outgoing") else "←"
        text = str(msg.get("text", ""))[:200]
        transcript_parts.append(f"{direction} {text}")

    transcript = "\n".join(transcript_parts)
    if len(transcript) > 8000:
        transcript = transcript[-8000:]
        space_idx = transcript.find(" ")
        if space_idx > 0:
            transcript = transcript[space_idx + 1 :]
        logger.debug("skill_creator: truncated transcript to %d chars", len(transcript))

    user_msg = (
        f"Проанализируй историю сообщений пользователя и предложи новые навыки.\n\n"
        f"ИСТОРИЯ СООБЩЕНИЙ (→ исходящие, ← входящие):\n"
        f"```\n{transcript}\n```\n\n"
        f"Предложи навыки в формате JSON-массива."
    )

    effective_max_tokens = (
        max_tokens if max_tokens is not None else settings.agent_token_budget
    )

    try:
        raw = await asyncio.wait_for(
            provider.chat(
                [
                    ChatMessage(role="system", content=SKILL_CREATOR_SYSTEM),
                    ChatMessage(role="user", content=user_msg),
                ],
                heavy=True,
                max_tokens=effective_max_tokens,
            ),
            timeout=_AGENT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "skill_creator_agent: LLM call timed out after %ds", _AGENT_TIMEOUT
        )
        return []
    except Exception as e:
        logger.error("Skill creator agent LLM error: %s", e)
        return []

    try:
        parsed = extract_json_from_llm_response(raw, default={"items": []})
        if isinstance(parsed, dict):
            proposals = parsed.get("items", [])
            if isinstance(proposals, list):
                return proposals
        logger.warning("Skill creator parse failed, raw: %s", raw[:200])
        return []
    except Exception as e:
        logger.warning("Skill creator parse error: %s, raw: %s", e, raw[:200])
        return []
