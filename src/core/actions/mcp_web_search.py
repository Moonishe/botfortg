"""MCP Tool: веб-поиск через DuckDuckGo."""

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any

from src.core.actions.tool_registry import tool
from src.core.infra.key_guard import safe_str
from src.core.security.web_sanitizer import sanitize_search_result

logger = logging.getLogger(__name__)

# ── Константы ──
_MAX_CACHE_SIZE: int = 256
_CACHE_TTL_SEC: float = 600.0
_MAX_CONCURRENT_SEARCHES: int = 3
_DDG_TIMEOUT_SEC: float = 15.0
_QUERY_TRUNCATE_CHARS: int = 300
_SEM_ACQUIRE_TIMEOUT_SEC: float = 15.0

# ── Кеш результатов поиска ──
_SEARCH_CACHE: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_SEARCH_SEM = asyncio.Semaphore(_MAX_CONCURRENT_SEARCHES)


def _cache_get(query_hash: str) -> dict | None:
    """Получить из кеша, удалить expired entries по пути."""
    if query_hash in _SEARCH_CACHE:
        ts, result = _SEARCH_CACHE[query_hash]
        if time.monotonic() - ts < _CACHE_TTL_SEC:
            _SEARCH_CACHE.move_to_end(query_hash)
            return result
        else:
            del _SEARCH_CACHE[query_hash]
    return None


def _cache_put(query_hash: str, result: dict) -> None:
    """Положить в кеш, вытеснить старые если > max."""
    _SEARCH_CACHE[query_hash] = (time.monotonic(), result)
    _SEARCH_CACHE.move_to_end(query_hash)
    while len(_SEARCH_CACHE) > _MAX_CACHE_SIZE:
        _SEARCH_CACHE.popitem(last=False)


@tool(
    name="web_search",
    description="Ищет в интернете и возвращает сниппеты. Используй когда не знаешь ответа — сначала поищи!",
    category="web",
    risk="low",
    params={
        "query": "str — поисковый запрос",
        "limit": "int — макс. результатов (1-10, по умолчанию 3)",
    },
)
async def web_search(
    query: str = "",
    limit: int = 3,
    **kwargs: Any,
) -> dict[str, Any]:
    if not query:
        return {"error": "query обязателен"}
    limit = max(1, min(10, limit))

    # ── Defense-in-depth truncation + normalization + cache lookup ──
    cache_key = query.strip().lower()[:_QUERY_TRUNCATE_CHARS]
    query_hash = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    cached = _cache_get(query_hash)
    if cached is not None:
        return cached

    try:
        from duckduckgo_search import DDGS

        try:
            await asyncio.wait_for(
                _SEARCH_SEM.acquire(), timeout=_SEM_ACQUIRE_TIMEOUT_SEC
            )
        except TimeoutError:
            return {"ok": False, "error": "search pool busy", "results": []}
        try:

            def _sync_search() -> list:
                ddgs = DDGS()
                try:
                    return list(ddgs.text(cache_key, max_results=limit))
                finally:
                    try:
                        ddgs.close()
                    except Exception:
                        logger.debug("Non-critical error", exc_info=True)

            results = await asyncio.wait_for(
                asyncio.to_thread(_sync_search), timeout=_DDG_TIMEOUT_SEC
            )

            if not results:
                return {"ok": True, "results": [], "query": cache_key}

            items = []
            for r in results:
                title, snippet = sanitize_search_result(
                    r.get("title", ""), r.get("body", "")
                )
                items.append(
                    {
                        "title": title,
                        "snippet": snippet,
                        "url": r.get("href", ""),
                    }
                )

            result = {"ok": True, "results": items, "query": cache_key}
            _cache_put(query_hash, result)
            return result
        finally:
            _SEARCH_SEM.release()

    except ImportError:
        return {
            "error": "duckduckgo-search не установлен. pip install duckduckgo-search"
        }
    except Exception as e:
        return {"error": safe_str(e)[:_QUERY_TRUNCATE_CHARS]}
