"""Подсистема мониторинга Telegram-каналов: разрешение сущностей, фетчинг, анализ."""

from src.core.monitor.source_resolver import resolve_source
from src.core.monitor.fetcher import check_periodic, fetch_history, match_rules
from src.core.monitor.analyzer import summarize_message

__all__ = [
    "resolve_source",
    "fetch_history",
    "match_rules",
    "check_periodic",
    "summarize_message",
]
