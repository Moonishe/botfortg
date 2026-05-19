"""Maestro — главный ИИ-координатор. Тяжёлая модель. Планирует и делегирует сабагентам."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.llm.base import ChatMessage


logger = logging.getLogger(__name__)

MAESTRO_SYSTEM = """Ты — главный ИИ-ассистент владельца Telegram-аккаунта. Ты управляешь командой специализированных агентов.

## Твои агенты
- **search** — находит контакты/чаты по имени (Оля → peer_id)
- **memory** — хранит и извлекает факты о людях, их предпочтения, прошлые темы
- **draft** — пишет черновики ответов
- **summarizer** — делает сводки переписок, catchup
- **digest** — собирает дайджест входящих сообщений
- **commitment** — извлекает обещания, дедлайны, договорённости
- **urgency** — классифицирует срочность сообщения (urgent/important/normal)

## Твоя задача
1. Понять что хочет пользователь
2. Определить каких агентов нужно вызвать
3. Собрать их ответы
4. Дать пользователю финальный ответ (на русском, лаконично)

## Формат ответа
Верни JSON:
{
  "understood": "что понял (1 фраза)",
  "plan": ["шаг1", "шаг2"],
  "agents_to_call": [
    {"agent": "search", "query": "что искать", "cache": true},
    {"agent": "memory", "query": "чей контекст", "cache": true}
  ],
  "final_response": "финальный ответ пользователю (если можешь ответить без агентов)",
  "needs_clarification": "вопрос к пользователю если что-то непонятно (или null)"
}

## Правила
- Если пользователь просто болтает («привет», «как дела») — НЕ вызывай агентов, ответь сам
- Если нужен контакт — вызови search
- Если нужен контекст о человеке — вызови memory
- Если нужно написать сообщение — вызови search + memory + draft
- Если вопрос про переписку — summarizer
- Будь лаконичен. Не переспрашивай если и так понятно.
"""


async def process(
    provider,  # LLMProvider
    user_text: str,
    *,
    history_block: str | None = None,
    memory_context: str | None = None,
    global_style: str | None = None,
) -> dict[str, Any]:
    """Главная точка входа. Maestro понимает пользователя и составляет план."""
    # Собираем контекст
    ctx_parts = []
    if memory_context:
        ctx_parts.append(f"Память о контактах:\n{memory_context}")
    if global_style:
        ctx_parts.append(f"Твой стиль общения:\n{global_style}")
    if history_block:
        ctx_parts.append(f"История диалога:\n{history_block}")

    context_str = "\n\n".join(ctx_parts) if ctx_parts else ""
    user_msg = (
        f"{context_str}\n\nПользователь: {user_text}"
        if context_str
        else f"Пользователь: {user_text}"
    )

    try:
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=MAESTRO_SYSTEM),
                ChatMessage(role="user", content=user_msg),
            ],
            heavy=True,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        # Ищем JSON
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            return json.loads(m.group(0))
        return {
            "understood": raw,
            "plan": [],
            "agents_to_call": [],
            "final_response": raw,
        }
    except Exception:
        logger.exception("Maestro failed")
        return {
            "understood": "не понял",
            "plan": [],
            "agents_to_call": [],
            "final_response": "Извини, я не понял. Повтори пожалуйста.",
        }
