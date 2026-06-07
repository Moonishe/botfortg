"""LLM-анализ сообщений: саммари, извлечение фактов, оценка важности."""

from __future__ import annotations

import logging

from src.llm.base import ChatMessage, TaskType

logger = logging.getLogger(__name__)


async def summarize_message(
    text: str,
    source_title: str = "",
    *,
    max_chars: int = 500,
) -> str:
    """Генерирует краткое (10-15 слов) LLM-саммари сообщения.

    Args:
        text: Текст сообщения.
        source_title: Название канала/чата для контекста.
        max_chars: Максимальная длина текста, подаваемого в LLM.

    Returns:
        Строка саммари. В случае ошибки возвращает усечённый исходный текст.
    """
    if not text or not text.strip():
        return "(пустое сообщение)"

    truncated = text[:max_chars].strip()

    # Lazy imports — избегаем циркулярных импортов
    from src.config import settings
    from src.db.repo import get_or_create_user
    from src.db.session import get_session
    from src.llm.provider_manager import build_provider

    try:
        async with get_session() as session:
            user = await get_or_create_user(session, settings.owner_telegram_id)
            provider = await build_provider(
                session, user, purpose="summarize", task_type=TaskType.SUMMARIZE
            )

            if provider is None:
                logger.warning("summarize_message: no LLM provider available")
                return _fallback_summary(text)

            context = f" из канала {source_title}" if source_title else ""
            prompt = (
                f"Одной строкой (10-15 слов) резюмируй сообщение{context}:\n\n"
                f"{truncated}"
            )

            result = await provider.chat(
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "Ты — ассистент анализа сообщений. Отвечай кратко, "
                            "одной строкой, только саммари без лишних слов."
                        ),
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                task_type=TaskType.SUMMARIZE,
            )

            summary = result.strip() if result else ""
            if summary:
                return summary

            return _fallback_summary(text)

    except Exception:
        logger.exception("summarize_message failed for source=%s", source_title)
        return _fallback_summary(text)


def _fallback_summary(text: str, max_len: int = 150) -> str:
    """Fallback-саммари без LLM — первые N символов текста."""
    clean = text.strip()
    if len(clean) <= max_len:
        return clean
    # Обрезаем по последнему пробелу
    cut = clean.rfind(" ", 0, max_len)
    if cut == -1:
        cut = max_len
    return clean[:cut] + "…"
