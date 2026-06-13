"""Auto-Tool Selection — маршрутизатор: пробелы MetaReasoner → ToolRegistry."""

from __future__ import annotations

import logging
from typing import Any

from src.config import settings
from src.core.rag.types import ToolAction

logger = logging.getLogger(__name__)

GAP_TOOL_MAP: dict[str, list[str]] = {
    "missing_recent_data": ["web_search_ddg"],
    "missing_academic_sources": ["pubmed_search"],
    "missing_multimedia": ["youtube_search"],
    "missing_statistics": ["web_search_ddg"],
    "missing_news": ["web_search_ddg"],
    "missing_documentation": ["web_search_ddg", "context7_search"],
    "missing_local_context": ["recall_memory"],
    "missing_social_media": ["x_rss", "web_search_ddg"],
    "missing_tweets": ["x_rss", "web_search_ddg"],
    "missing_realtime_news": ["x_rss", "web_search_news", "web_search_ddg"],
    "missing_breaking_news": ["x_rss", "web_search_news"],
    "missing_lyrics": ["genius", "web_search_ddg"],
    "missing_exchange_rates": ["mcp_exchange", "web_search_ddg"],
    "missing_images": ["mcp_photo_search", "web_search_ddg"],
    "missing_telegram_channels": ["telegram_public", "web_search_ddg"],
}


class ToolSelector:
    """Маршрутизатор: пробелы → инструменты."""

    async def select_tools(
        self,
        gaps: list[str],
        context: dict[str, Any] | None = None,
    ) -> list[ToolAction]:
        """Выбрать инструменты для списка пробелов."""
        if not settings.deep_research_auto_tools_enabled or not gaps:
            return []

        actions: list[ToolAction] = []
        context = context or {}

        for gap in gaps:
            gap_lower = gap.lower()
            tools = self._classify_gap(gap_lower)
            for tool in tools[: settings.tool_selector_max_tools]:
                actions.append(
                    ToolAction(
                        tool_name=tool,
                        params={"query": context.get("query", gap)},
                        priority=0,
                        reason=f"gap: {gap[:80]}",
                    )
                )

        # Deduplicate
        seen: set[tuple[str, str]] = set()
        unique: list[ToolAction] = []
        for a in actions:
            query_val = str(a.params.get("query", ""))
            key = (a.tool_name, query_val)
            if key not in seen:
                seen.add(key)
                unique.append(a)

        logger.debug(
            "ToolSelector: %d tools selected for %d gaps", len(unique), len(gaps)
        )
        return unique

    @staticmethod
    def _classify_gap(gap: str) -> list[str]:
        """Классифицировать пробел: сначала GAP_TOOL_MAP, затем keyword fallback."""
        gap_lower = gap.lower()

        # Phase 1: Direct match — check if gap string matches any GAP_TOOL_MAP key
        for key, tools in GAP_TOOL_MAP.items():
            key_normalized = key.replace("_", " ").lower()
            if key_normalized in gap_lower or gap_lower in key_normalized:
                return list(tools)

        # Phase 2: Keyword-based classification → resolve to GAP_TOOL_MAP key
        gap_key = ToolSelector._resolve_gap_key(gap_lower)
        if gap_key is not None and gap_key in GAP_TOOL_MAP:
            return list(GAP_TOOL_MAP[gap_key])

        # Default fallback
        return ["web_search_ddg"]

    @staticmethod
    def _resolve_gap_key(gap: str) -> str | None:
        """Разрешить описание пробела в ключ GAP_TOOL_MAP по ключевым словам."""
        # X/Twitter / social media
        if any(
            w in gap
            for w in (
                "tweet",
                "twitter",
                "x.com",
                "nitter",
                "твит",
                "твиттер",
                "social media",
                "социальные сети",
            )
        ):
            return "missing_social_media"

        # Academic/medical keywords
        if any(
            w in gap
            for w in ("pubmed", "медицин", "health", "disease", "лечени", "научн")
        ):
            return "missing_academic_sources"

        # YouTube/video keywords
        if any(w in gap for w in ("youtube", "video", "туториал", "урок", "гайд")):
            return "missing_multimedia"

        # Documentation keywords
        if any(w in gap for w in ("api", "documentation", "docs", "sdk", "библиотек")):
            return "missing_documentation"

        # Local context
        if any(w in gap for w in ("memory", "recall", "контекст", "истори", "помн")):
            return "missing_local_context"

        # News keywords
        if any(w in gap for w in ("news", "новост", "breaking", "срочн")):
            return "missing_news"

        # Statistics keywords
        if any(w in gap for w in ("stat", "статисти", "цифр", "данн")):
            return "missing_statistics"

        # Lyrics / music keywords
        if any(
            w in gap
            for w in (
                "lyrics",
                "текст песни",
                "слова песни",
                "песня",
                "исполнитель",
                "genius",
            )
        ):
            return "missing_lyrics"

        # Exchange rates / currency keywords
        if any(
            w in gap
            for w in (
                "курс валют",
                "валюта",
                "обмен",
                "usd",
                "eur",
                "rub",
                "exchange rate",
                "конверт",
            )
        ):
            return "missing_exchange_rates"

        # Image / photo search keywords
        if any(
            w in gap
            for w in (
                "картинка",
                "фото",
                "изображение",
                "картинки",
                "image",
                "picture",
                "фотограф",
            )
        ):
            return "missing_images"

        # Telegram channel keywords
        if any(
            w in gap
            for w in (
                "телеграм канал",
                "telegram канал",
                "public channel",
                "t.me/",
                "tg канал",
                "чат канал",
            )
        ):
            return "missing_telegram_channels"

        return None
