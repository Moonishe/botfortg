"""MCP Tool: глубокое исследование (deep research).

Позволяет LLM запускать двухфазное исследование по поисковому запросу
с параллельной загрузкой веб-источников и сохранением результатов.

Если передан Telegram-объект ``message``, прогресс исследования
стримится в реальном времени — сообщение редактируется с эмодзи-фазами.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import tool
from src.core.rag.deep_research_pipeline import get_deep_research_pipeline
from src.core.rag.types import ResearchRequest, ResearchStatus

logger = logging.getLogger(__name__)

# Эмодзи для фаз прогресса
PHASE_EMOJI: dict[str, str] = {
    "searching": "🔍",
    "deep_dive": "📖",
    "cross_ref": "⚔️",
    "synthesis": "🧩",
    "memory_seed": "🧠",
    "timeline": "⏱️",
    "completed": "✅",
    "failed": "❌",
}


def _build_progress_callback(message: Any) -> Any:
    """Создать callback для стриминга прогресса в Telegram сообщение.

    Args:
        message: Объект aiogram.types.Message для редактирования.

    Returns:
        Async callback (job_id, phase, detail) -> None.
    """

    async def _on_progress(job_id: str, phase: str, detail: str = "") -> None:
        emoji = PHASE_EMOJI.get(phase, "🔄")
        text = (
            f"{emoji} **Deep Research** `{job_id[:8]}`\n{detail}"
            if detail
            else f"{emoji} **Deep Research** `{job_id[:8]}`"
        )
        try:
            await message.edit_text(text)
        except Exception:
            logger.debug(
                "Progress callback: failed to edit Telegram message (non-critical)",
                exc_info=True,
            )

    return _on_progress


@tool(
    name="deep_research",
    description=(
        "Глубокое исследование темы с веб-поиском и параллельной загрузкой источников. "
        "Возвращает job_id для отслеживания статуса. "
        "Используй для сложных вопросов, требующих сбора информации из нескольких источников."
    ),
    category="research",
    risk="low",
    params={
        "query": "str — поисковый запрос для исследования",
        "max_minutes": "int — макс. время в минутах (1-10, по умолчанию 5)",
    },
)
async def deep_research(
    query: str = "",
    max_minutes: int = 5,
    **kwargs: Any,
) -> dict[str, Any]:
    """Запустить глубокое исследование по запросу.

    Args:
        query: Поисковый запрос. Обязателен.
        max_minutes: Максимальное время выполнения (1–10, по умолчанию 5).

    Returns:
        dict с ключами: ok, job_id, status, message, _streaming.
    """
    if not query or not query.strip():
        return {"ok": False, "error": "query обязателен"}

    # Санитизация max_minutes
    max_minutes = max(1, min(10, max_minutes))

    # Проверяем, есть ли Telegram message для стриминга
    message = kwargs.get("message")
    _has_streaming = message is not None

    try:
        pipeline = get_deep_research_pipeline()
        request = ResearchRequest(
            query=query.strip(),
            max_minutes=max_minutes,
        )

        # Если есть Telegram-сообщение — включаем стриминг прогресса
        if _has_streaming:
            try:
                pipeline.set_progress_callback(_build_progress_callback(message))
            except Exception:
                logger.debug(
                    "Failed to set progress callback, falling back to logging",
                    exc_info=True,
                )
                _has_streaming = False

        job_id = pipeline.submit(request)

        # Если стриминг — редактируем сообщение с начальным статусом
        if _has_streaming:
            try:
                await message.edit_text(
                    f"🔍 **Deep Research** `{job_id[:8]}`\nЗапускаю исследование…\n\n📝 {query[:200]}"
                )
            except Exception:
                logger.debug("Failed to send initial streaming message", exc_info=True)

        # Получаем начальный статус
        result = await pipeline.get_status(job_id)
        status = result.status.value if result else ResearchStatus.PENDING.value

        logger.info(
            "Deep research started: job_id=%s query=%r max_minutes=%d streaming=%s",
            job_id,
            query,
            max_minutes,
            _has_streaming,
        )

        response: dict[str, Any] = {
            "ok": True,
            "job_id": job_id,
            "status": status,
            "message": (
                f"Исследование запущено. Job ID: {job_id}. "
                f"Статус: {status}. "
                f"Результаты будут сохранены в data/research/{job_id}/"
            ),
        }
        # Флаг для free_text — не перезаписывать сообщение,
        # т.к. прогресс стримится асинхронно
        if _has_streaming:
            response["_streaming"] = True
        return response

    except Exception as exc:
        logger.exception("deep_research failed for query %r", query)
        return {"ok": False, "error": str(exc)}
