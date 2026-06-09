"""MCP Tool: глубокое исследование (deep research).

Позволяет LLM запускать двухфазное исследование по поисковому запросу
с параллельной загрузкой веб-источников и сохранением результатов.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import tool
from src.core.rag.deep_research_pipeline import get_deep_research_pipeline
from src.core.rag.types import ResearchRequest, ResearchStatus

logger = logging.getLogger(__name__)


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
        dict с ключами: ok, job_id, status, message.
    """
    if not query or not query.strip():
        return {"ok": False, "error": "query обязателен"}

    # Санитизация max_minutes
    max_minutes = max(1, min(10, max_minutes))

    try:
        pipeline = get_deep_research_pipeline()
        request = ResearchRequest(
            query=query.strip(),
            max_minutes=max_minutes,
        )
        job_id = pipeline.submit(request)

        # Получаем начальный статус
        result = await pipeline.get_status(job_id)
        status = result.status.value if result else ResearchStatus.PENDING.value

        logger.info(
            "Deep research started: job_id=%s query=%r max_minutes=%d",
            job_id,
            query,
            max_minutes,
        )

        return {
            "ok": True,
            "job_id": job_id,
            "status": status,
            "message": (
                f"Исследование запущено. Job ID: {job_id}. "
                f"Статус: {status}. "
                f"Результаты будут сохранены в data/research/{job_id}/"
            ),
        }

    except Exception as exc:
        logger.exception("deep_research failed for query %r", query)
        return {"ok": False, "error": str(exc)}
