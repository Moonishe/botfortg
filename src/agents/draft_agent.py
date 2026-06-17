"""Draft Agent — генерирует черновики ответов на входящие сообщения."""

from __future__ import annotations

import logging
from typing import Any

from src.agents._json_utils import extract_json_from_llm_response
from src.llm.base import ChatMessage

logger = logging.getLogger(__name__)

DRAFT_SYSTEM = """Ты — AI-ассистент в Telegram. Пиши черновики ответов на входящие сообщения.

Стиль: лаконично (1-3 предложения), на русском, в стиле владельца.
Учитывай style_hint (стиль), memory_hint (память), absence_hint (статус).
Если владелец absent — не обещай быстрого ответа.

Верни ТОЛЬКО JSON:
{
  "draft": "текст черновика",
  "tone": "warm|friendly|professional|cold",
  "reasoning": "почему такой тон (1 фраза)"
}
"""


async def draft(
    provider,
    sender_name: str,
    incoming_text: str,
    *,
    history_text: str | None = None,
    style_hint: str | None = None,
    memory_hint: str | None = None,
    absence_hint: str | None = None,
) -> dict[str, Any]:
    """Генерирует черновик ответа на входящее сообщение.

    Args:
        provider: Объект LLMProvider с методом chat().
        sender_name: Имя отправителя.
        incoming_text: Текст входящего сообщения.
        history_text: Контекст предыдущей переписки.
        style_hint: Подсказка о стиле владельца.
        memory_hint: Подсказка сохранённых фактов о собеседнике.
        absence_hint: Статус отсутствия владельца.

    Returns:
        Словарь с ключами draft (str), tone (str), reasoning (str).
    """
    parts = [f"Собеседник: {sender_name}"]
    if history_text:
        parts.append(f"Контекст переписки:\n{history_text[:1500]}")
    parts.append(f"Входящее сообщение: {incoming_text}")

    hints = []
    if style_hint:
        hints.append(f"СТИЛЬ ВЛАДЕЛЬЦА:\n{style_hint}")
    if memory_hint:
        hints.append(f"ПАМЯТЬ О СОБЕСЕДНИКЕ:\n{memory_hint}")
    if absence_hint:
        hints.append(f"СТАТУС ВЛАДЕЛЬЦА:\n{absence_hint}")

    if hints:
        parts.append("\n\n".join(hints))

    parts.append("Напиши черновик ответа.")
    user_msg = "\n\n".join(parts)

    try:
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=DRAFT_SYSTEM),
                ChatMessage(role="user", content=user_msg),
            ],
            heavy=False,
        )
    except Exception as e:
        logger.error("Draft agent LLM error: %s", e, exc_info=True)
        return {
            "draft": "Извини, не могу сейчас ответить.",
            "tone": "neutral",
            "reasoning": "fallback",
        }
    parsed = extract_json_from_llm_response(raw)
    if parsed is not None:
        return parsed
    return {"draft": raw.strip(), "tone": "neutral", "reasoning": "raw output"}


async def draft_variants(
    provider,
    sender_name: str,
    incoming_text: str,
) -> list[dict]:
    """Generate 3 tone variants: neutral, warm, brief.

    Args:
        provider: Объект LLMProvider с методом chat().
        sender_name: Имя отправителя.
        incoming_text: Текст входящего сообщения.

    Returns:
        Список словарей с ключами tone (str) и text (str).
    """
    prompt = (
        f"Сгенерируй 3 варианта ответа в разных тонах для контакта {sender_name}.\n"
        f"Входящее: {incoming_text}\n\n"
        "Верни ТОЛЬКО JSON:\n"
        '{"variants": [\n'
        '  {"tone": "нейтральный", "text": "..."},\n'
        '  {"tone": "тёплый", "text": "..."},\n'
        '  {"tone": "краткий", "text": "..."}\n'
        "]}"
    )
    try:
        raw = await provider.chat(
            [ChatMessage(role="user", content=prompt)],
            heavy=False,
        )
        parsed = extract_json_from_llm_response(raw)
        if parsed is not None:
            return parsed.get("variants", [])
    except Exception:
        logger.debug("draft_variants failed", exc_info=True)
    return []
