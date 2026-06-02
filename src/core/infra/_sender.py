"""Protocol for message sending — core layer contract, implemented in bot layer."""

from __future__ import annotations

from typing import Any, Protocol


class MessageSender(Protocol):
    """Protocol for sending Telegram messages. Implementation in bot layer."""

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: Any = None,
    ) -> None: ...

    async def send_document(
        self,
        chat_id: int,
        document: Any,
        *,
        caption: str = "",
        parse_mode: str = "HTML",
        filename: str = "file",
    ) -> None: ...
