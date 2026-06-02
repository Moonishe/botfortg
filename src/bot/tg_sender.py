"""Централизованная отправка сообщений с retry/backoff и авто-санитацией HTML."""

from __future__ import annotations

from typing import Any

from src.core.infra._retry import send_with_retry
from src.core.infra.text_sanitizer import sanitize_html


async def safe_send(
    bot: Any,
    chat_id: int | str,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    reply_markup: Any = None,
    **kwargs: Any,
) -> Any:
    """Отправить сообщение с авто-санитацией HTML и retry/backoff.

    Если parse_mode="HTML", текст автоматически прогоняется через
    sanitize_html() перед отправкой.
    """
    safe_text = sanitize_html(text) if parse_mode == "HTML" else text
    return await send_with_retry(
        bot.send_message,
        chat_id=chat_id,
        text=safe_text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        **kwargs,
    )
