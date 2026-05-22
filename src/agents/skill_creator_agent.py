"""Skill Creator Agent — анализирует историю сообщений и предлагает новые навыки."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.llm.base import ChatMessage

logger = logging.getLogger(__name__)

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
    provider,
    recent_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Анализирует историю сообщений и предлагает новые навыки.

    Args:
        provider: Объект LLMProvider с методом chat().
        recent_messages: Список сообщений вида
            [{"text": "...", "is_outgoing": bool, "timestamp": "..."}, ...].

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

    user_msg = (
        f"Проанализируй историю сообщений пользователя и предложи новые навыки.\n\n"
        f"ИСТОРИЯ СООБЩЕНИЙ (→ исходящие, ← входящие):\n"
        f"```\n{transcript}\n```\n\n"
        f"Предложи навыки в формате JSON-массива."
    )

    try:
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=SKILL_CREATOR_SYSTEM),
                ChatMessage(role="user", content=user_msg),
            ],
            heavy=True,
        )
    except Exception as e:
        logger.error("Skill creator agent LLM error: %s", e)
        return []

    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json|JSON)?\s*\n?", "", raw)
        raw = re.sub(r"\n?\s*```\s*$", "", raw)

    try:
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            proposals = json.loads(m.group(0))
            if isinstance(proposals, list):
                return proposals
        logger.debug("Skill creator parse failed, raw: %s", raw[:200])
        return []
    except (json.JSONDecodeError, Exception) as e:
        logger.debug("Skill creator parse error: %s, raw: %s", e, raw[:200])
        return []
