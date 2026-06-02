"""Protocol for core → userbot communication. Implemented by the userbot layer."""

from __future__ import annotations
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from telethon import TelegramClient
    from src.db.models import User


class UserbotGateway(Protocol):
    """Single gateway from core layer to userbot layer."""

    def get_client(self, telegram_id: int) -> "TelegramClient | None":
        """Return active Telethon client for user, or None if not started."""
        ...

    async def sync_dialogs(
        self, client: "TelegramClient", user: "User", *, limit: int = 500
    ) -> dict:
        """Sync Telegram dialogs to local DB. Returns stats dict."""
        ...


# Module-level registry
_gateway: UserbotGateway | None = None


def set_userbot_gateway(gw: UserbotGateway) -> None:
    global _gateway
    if _gateway is not None:
        raise RuntimeError(
            "UserbotGateway already set — call set_userbot_gateway() only once"
        )
    _gateway = gw


def get_userbot_gateway() -> UserbotGateway:
    if _gateway is None:
        raise RuntimeError(
            "UserbotGateway not set — call set_userbot_gateway() during startup"
        )
    return _gateway
