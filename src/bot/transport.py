"""Transport abstraction for bot serving (polling vs webhook)."""

from __future__ import annotations

import logging
from typing import Protocol

from aiogram import Bot, Dispatcher


logger = logging.getLogger(__name__)


class BotTransport(Protocol):
    """Protocol for bot serving transport. Implementations: PollingTransport, WebhookTransport."""

    async def serve(self, bot: Bot, dp: Dispatcher, **kwargs: object) -> None:
        """Start serving updates. Blocks until shutdown."""
        ...

    async def shutdown(self) -> None:
        """Gracefully stop the transport."""
        ...


class PollingTransport:
    """Long-polling transport (default)."""

    async def serve(self, bot: Bot, dp: Dispatcher, **kwargs: object) -> None:
        allowed_updates = kwargs.get("allowed_updates", dp.resolve_used_update_types())
        logger.info("Starting polling transport\u2026")
        await dp.start_polling(
            bot, allowed_updates=allowed_updates, close_bot_session=False
        )

    async def shutdown(self) -> None:
        logger.info("Polling transport stopped")


class WebhookTransport:
    """Webhook transport (skeleton \u2014 requires external HTTPS server).

    Full implementation needs: aiohttp/uvicorn, SSL cert, domain, Redis FSM storage.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8443) -> None:
        self._host = host
        self._port = port

    async def serve(self, bot: Bot, dp: Dispatcher, **kwargs: object) -> None:
        logger.info("Starting webhook transport on %s:%d…", self._host, self._port)
        # ACTION (feature): полноценная webhook-реализация —
        # set_webhook + aiohttp server. Пока используем long-polling.
        raise NotImplementedError(
            "WebhookTransport is a skeleton \u2014 implement aiohttp server"
        )

    async def shutdown(self) -> None:
        logger.info("Webhook transport stopped")


def get_transport() -> BotTransport:
    """Factory: returns transport based on settings.transport_mode."""
    from src.config import settings

    mode = settings.transport_mode
    if mode == "webhook":
        logger.warning(
            "WebhookTransport not yet implemented, falling back to PollingTransport"
        )
        return PollingTransport()
    if mode != "polling":
        logger.warning("Unknown transport_mode=%r, falling back to polling", mode)
    return PollingTransport()
