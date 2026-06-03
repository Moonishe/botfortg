"""MCP Tool: PubMed научный поиск через NCBI API."""

import logging
from typing import Any

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

        try:
            # Поиск статей (возвращает список dict с метаинформацией)
            articles = await pubmed_client.search_pubmed(
                query=query,
                max_results=max_results,
            )

            if not articles:
                return {
                    "ok": True,
                    "query": query,
                    "count": 0,
                    "articles": [],
                    "message": "Статьи не найдены",
                }

            # Если есть PMIDs, получаем детальные summaries
            pmids = [art.get("pmid", "") for art in articles if art.get("pmid")]
            summaries = []
            if pmids:
                try:
                    summaries = await pubmed_client.fetch_summaries(pmids)
                except Exception as e:
                    logger.warning("fetch_summaries failed: %s", str(e))

            # Форматирование результатов
            items = []
            for article in articles:
                item = {
                    "title": article.get("title", ""),
                    "authors": article.get("authors", []),
                    "journal": article.get("journal", ""),
                    "year": article.get("year", ""),
                    "pmid": article.get("pmid", ""),
                    "url": article.get("url", ""),
                }

                # Добавляем абстракт если доступен
                pmid = article.get("pmid", "")
                if pmid:
                    try:
                        abstract = await pubmed_client.fetch_abstract(pmid)
                        if abstract:
                            item["abstract"] = abstract
                    except Exception as e:
                        logger.debug(
                            "fetch_abstract failed for PMID %s: %s", pmid, str(e)
                        )

                items.append(item)

            return {
                "ok": True,
                "query": query,
                "count": len(items),
                "articles": items,
            }

        except Exception as e:
            logger.warning("PubMed search failed: %s", str(e))
            return {"error": f"Ошибка поиска в PubMed: {str(e)[:200]}"}

    except ImportError:
        return {"error": "pubmed_client не установлен или недоступен"}
    except Exception as e:
        logger.error("Unexpected error in pubmed_search: %s", e)
        return {"error": f"Непредвиденная ошибка: {str(e)[:200]}"}
