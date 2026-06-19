"""mcp_photo_search — поиск фото в интернете через DuckDuckGo."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urljoin

import httpx
from aiogram.types import BufferedInputFile

from src.config import settings
from src.core.actions.tool_registry import tool
from src.core.security.ssrf_guard import _check_ssrf_async

logger = logging.getLogger(__name__)

_MAX_RESULTS = 10
_DOWNLOAD_TIMEOUT = 15.0
_MAX_IMAGE_MB = 10  # DoS-защита


@tool(
    name="mcp_photo_search",
    description=(
        "Поиск изображений в интернете через DuckDuckGo. Два действия:\n"
        "- 'search_images' — поиск фото по текстовому запросу, возвращает до 10 URL. "
        "Примеры запросов: «котики», «sunset mountains».\n"
        "- 'send_image' — скачать картинку по URL и отправить в текущий чат Telegram"
    ),
    category="search",
    risk="low",
    params={
        "action": "str — 'search_images' или 'send_image'",
        "query": "str — поисковый запрос (для search_images)",
        "url": "str — URL картинки (для send_image)",
        "max_results": "int — макс. результатов (1-10, по умолчанию 10)",
    },
)
async def mcp_photo_search(
    action: str,
    query: str = "",
    url: str = "",
    max_results: int = 10,
    **kwargs: Any,
) -> dict[str, Any]:
    """Поиск фото/картинок в интернете и отправка в чат.

    Args:
        action: ``"search_images"`` или ``"send_image"``.
        query: Поисковый запрос (обязателен для ``search_images``).
        url: URL картинки (обязателен для ``send_image``).
        max_results: Максимум результатов (1-10, по умолчанию 10).

    Keyword Args (инжектятся рантаймом):
        _bot: экземпляр aiogram ``Bot``.
        _chat_id: ID целевого чата.
    """
    try:
        if action == "search_images":
            return await _search_images(query, max_results)
        if action == "send_image":
            return await _send_image(url, **kwargs)
        return {
            "error": (
                f"Неизвестное действие {action!r}. Допустимы: search_images, send_image"
            )
        }
    except Exception as exc:
        logger.exception("mcp_photo_search(%r) failed", action)
        return {"error": str(exc)}


# ── action: search_images ────────────────────────────────────────────────


async def _search_images(query: str, max_results: int) -> dict[str, Any]:
    """Поиск картинок через DuckDuckGo."""
    if not query or not query.strip():
        return {"error": "query обязателен для search_images"}
    limit = max(1, min(max_results, _MAX_RESULTS))

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return {
            "error": "duckduckgo-search не установлен. pip install duckduckgo-search"
        }

    def _sync_search() -> list[dict[str, Any]]:
        with DDGS() as ddgs:
            results = list(ddgs.images(query.strip(), max_results=limit))
        items: list[dict[str, Any]] = []
        for r in results:
            items.append(
                {
                    "title": r.get("title", ""),
                    "image_url": r.get("image", ""),
                    "thumbnail_url": r.get("thumbnail", ""),
                    "source_url": r.get("url", ""),
                    "width": r.get("width", 0),
                    "height": r.get("height", 0),
                    "source": r.get("source", ""),
                }
            )
        return items

    try:
        items = await asyncio.to_thread(_sync_search)
    except Exception as exc:
        logger.warning("DDGS images search failed: %s", exc)
        return {"error": f"Поиск не удался: {exc}"}

    return {"ok": True, "query": query.strip(), "results": items, "count": len(items)}


# ── action: send_image ───────────────────────────────────────────────────


async def _send_image(url: str, **kwargs: Any) -> dict[str, Any]:
    """Скачать фото по URL и отправить в чат."""
    if not url or not url.strip():
        return {"error": "url обязателен для send_image"}
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "URL должен начинаться с http:// или https://"}

    # Скачивание с SSRF-проверкой на каждом редиректе
    _MAX_REDIRECTS = 5
    try:
        async with httpx.AsyncClient(
            timeout=_DOWNLOAD_TIMEOUT, follow_redirects=False
        ) as client:
            current_url = url
            for redirect_count in range(_MAX_REDIRECTS + 1):
                # SSRF-защита для текущего URL
                ssrf_error = await _check_ssrf_async(current_url)
                if ssrf_error:
                    return ssrf_error

                resp = await client.get(current_url)

                # Ручная обработка редиректов
                if resp.status_code in (301, 302, 303, 307, 308):
                    if redirect_count >= _MAX_REDIRECTS:
                        return {"error": "Слишком много редиректов"}
                    location = resp.headers.get("location")
                    if not location:
                        return {"error": "Redirect без Location заголовка"}
                    current_url = urljoin(current_url, location)
                    continue

                resp.raise_for_status()
                break

            content_type = resp.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                return {
                    "error": f"URL не является картинкой (Content-Type: {content_type})"
                }
            data = resp.content
            if len(data) > _MAX_IMAGE_MB * 1024 * 1024:
                return {"error": f"Картинка слишком большая (>{_MAX_IMAGE_MB}MB)"}
    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code} при скачивании"}
    except httpx.RequestError as exc:
        return {"error": f"Ошибка скачивания: {exc}"}

    # Отправка в Telegram
    bot = kwargs.get("_bot")
    if bot is None:
        from src.core.infra.notifier import notifier

        bot = notifier.get_bot()
    if bot is None:
        return {"error": "Нет экземпляра бота — control bot не запущен"}

    chat_id = kwargs.get("_chat_id") or settings.owner_telegram_id
    if not chat_id:
        return {"error": "Не указан chat_id (owner не настроен)"}

    try:
        ext = url.rsplit(".", 1)[-1].split("?")[0] or "jpg"
        filename = f"photo.{ext}" if ext.isalpha() and len(ext) <= 4 else "photo.jpg"
        buf = BufferedInputFile(data, filename=filename)
        msg = await bot.send_photo(chat_id=chat_id, photo=buf, caption=f"🔍 {url}")
        return {
            "ok": True,
            "url": url,
            "message_id": msg.message_id,
            "size_kb": round(len(data) / 1024, 1),
        }
    except Exception as exc:
        logger.exception("send_photo failed for %r", url)
        return {"error": f"Не удалось отправить фото: {exc}"}
