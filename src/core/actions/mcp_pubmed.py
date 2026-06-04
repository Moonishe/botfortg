"""MCP Tool: PubMed научный поиск через NCBI API."""

import asyncio
import logging
from typing import Any

import httpx

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)

_QUERY_MAX_CHARS = 500


@tool(
    name="pubmed_search",
    description="Ищет научные статьи в PubMed. Используй когда нужны рецензированные исследования, клинические данные, биомедицинская литература.",
    category="research",
    risk="low",
    params={
        "query": "str — поисковый запрос на английском (например: 'CRISPR gene editing' или 'aspirin cardiovascular')",
        "max_results": "int — максимальное количество статей (1-20, по умолчанию 5)",
    },
)
async def pubmed_search(
    query: str = "",
    max_results: int = 5,
    **kwargs: Any,
) -> dict[str, Any]:
    """Поиск научных статей в PubMed через NCBI E-utilities API."""
    if not query or not query.strip():
        return {"error": "query обязателен"}

    query = query.strip()
    if len(query) > _QUERY_MAX_CHARS:
        query = query[:_QUERY_MAX_CHARS]

    # Валидация max_results
    max_results = max(1, min(20, max_results))

    try:
        # Late import чтобы не блокировать bootstrap
        from src.core.actions import pubmed_client
    except ImportError:
        return {"error": "pubmed_client не установлен или недоступен"}

    try:
        # Поиск PMIDs
        pmid_dicts = await pubmed_client.search_pubmed(
            query=query,
            max_results=max_results,
        )

        if not pmid_dicts:
            return {
                "ok": True,
                "query": query,
                "count": 0,
                "articles": [],
                "message": "Статьи не найдены",
            }

        # Получаем детальные summaries (метаданные статей)
        pmids = [art["pmid"] for art in pmid_dicts]
        articles = await pubmed_client.fetch_summaries(pmids)

        # Параллельная загрузка abstract-ов с индивидуальным таймаутом
        async def _fetch_abstract_with_timeout(pmid: str) -> str | None:
            try:
                return await asyncio.wait_for(
                    pubmed_client.fetch_abstract(pmid), timeout=10.0
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning("Failed to fetch abstract for PMID %s: %s", pmid, e)
                return None

        abstracts = await asyncio.gather(
            *[_fetch_abstract_with_timeout(art["pmid"]) for art in articles]
        )

        for article, abstract in zip(articles, abstracts):
            if abstract:
                article["abstract"] = abstract

        return {
            "ok": True,
            "query": query,
            "count": len(articles),
            "articles": articles,
        }

    except httpx.HTTPStatusError as e:
        logger.error("PubMed API error: %s - %s", e.response.status_code, e)
        return {"error": f"PubMed API error: {e.response.status_code}"}
    except httpx.RequestError as e:
        logger.error("PubMed request failed: %s", e)
        return {"error": "PubMed service temporarily unavailable"}
    except asyncio.TimeoutError:
        logger.error("PubMed request timeout")
        return {"error": "PubMed request timeout"}
    except Exception as e:
        logger.exception("Unexpected error in pubmed_search: %s", e)
        return {"error": f"Непредвиденная ошибка: {str(e)[:200]}"}
