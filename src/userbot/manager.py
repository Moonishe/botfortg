import asyncio
import logging
from dataclasses import dataclass, field

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession

from src.config import parse_telethon_proxy, settings
from src.db.repo import load_telegram_session
from src.db.session import get_session


logger = logging.getLogger(__name__)


@dataclass
class PendingLogin:
    # Промежуточное состояние логина: между запросами кода и 2FA-пароля

    client: TelegramClient
    api_id: int
    api_hash: str
    phone: str | None = None
    phone_code_hash: str | None = None


_MANAGER_SINGLETON: "UserbotManager | None" = None


@dataclass
class UserbotManager:
    _clients: dict[int, TelegramClient] = field(default_factory=dict)
    _pending: dict[int, PendingLogin] = field(default_factory=dict)
    _retry_tasks: set[asyncio.Task] = field(default_factory=set)

    def __post_init__(self) -> None:
        global _MANAGER_SINGLETON
        _MANAGER_SINGLETON = self

    async def restore_all(self) -> None:
        async with get_session() as session:
            from src.db.models import User
            from sqlalchemy import select

            users = (await session.execute(select(User))).scalars().all()
            for user in users:
                creds = await load_telegram_session(session, user)
                if creds is None:
                    continue
                api_id, api_hash, session_string = creds
                client = TelegramClient(
                    StringSession(session_string),
                    api_id,
                    api_hash,
                    proxy=parse_telethon_proxy(settings.proxy_url),
                )
                try:
                    await client.connect()
                    if await client.is_user_authorized():
                        self._clients[user.telegram_id] = client
                        from src.userbot.auto_reply import attach_auto_reply
                        from src.userbot.dialog_events import (
                            attach_dialog_event_handlers,
                        )
                        from src.userbot.mirror import attach_mirror

                        attach_auto_reply(client, user.telegram_id)
                        attach_dialog_event_handlers(client, user.telegram_id)
                        attach_mirror(client, user.telegram_id)
                        logger.info(
                            "Restored Telethon client for user %s", user.telegram_id
                        )
                    else:
                        await client.disconnect()
                        logger.warning(
                            "Saved session for %s is not authorized anymore",
                            user.telegram_id,
                        )
                        from src.core.scheduling.notification_queue import (
                            notification_queue,
                        )

                        await notification_queue.enqueue(
                            topic=f"userbot:{user.telegram_id}",
                            text="🔐 Сессия Telegram протухла. Нужен повторный /login.",
                            priority=1,  # Notification.PRIORITY_HIGH
                        )
                except FloodWaitError as e:
                    logger.warning(
                        "FloodWait %ds for user %s, sleeping...",
                        e.seconds,
                        user.telegram_id,
                    )
                    await asyncio.sleep(e.seconds)
                    await client.connect()
                    if await client.is_user_authorized():
                        self._clients[user.telegram_id] = client
                        from src.userbot.auto_reply import attach_auto_reply
                        from src.userbot.dialog_events import (
                            attach_dialog_event_handlers,
                        )
                        from src.userbot.mirror import attach_mirror

                        attach_auto_reply(client, user.telegram_id)
                        attach_dialog_event_handlers(client, user.telegram_id)
                        attach_mirror(client, user.telegram_id)
                        logger.info(
                            "FloodWait retry succeeded for user %s",
                            user.telegram_id,
                        )
                    else:
                        await client.disconnect()
                        logger.warning(
                            "FloodWait retry: session expired for %s",
                            user.telegram_id,
                        )
                except (RPCError, ConnectionError):
                    logger.exception(
                        "Failed to restore client for user %s", user.telegram_id
                    )
                    if client.is_connected():
                        await client.disconnect()

    def get_client(self, telegram_id: int) -> TelegramClient | None:
        return self._clients.get(telegram_id)

    def register_client(self, telegram_id: int, client: TelegramClient) -> None:
        self._clients[telegram_id] = client
        from src.userbot.auto_reply import attach_auto_reply
        from src.userbot.dialog_events import attach_dialog_event_handlers
        from src.userbot.mirror import attach_mirror

        attach_auto_reply(client, telegram_id)
        attach_dialog_event_handlers(client, telegram_id)
        attach_mirror(client, telegram_id)

    async def remove_client(self, telegram_id: int) -> None:
        client = self._clients.get(telegram_id)
        if client is not None:
            try:
                await client.log_out()
            except RPCError:
                logger.exception("log_out failed")
            try:
                await client.disconnect()
            except RPCError:
                logger.exception("userbot disconnect failed")
            finally:
                self._clients.pop(telegram_id, None)
                # Очистка attached-множеств — предотвращает утечку id(client)
                _cid = id(client)
                try:
                    from src.userbot.mirror import _attached_mirror_clients

                    _attached_mirror_clients.discard(_cid)
                except ImportError:
                    pass
                try:
                    from src.userbot.auto_reply import _attached_auto_reply_clients

                    _attached_auto_reply_clients.discard(_cid)
                except ImportError:
                    pass
                try:
                    from src.userbot.dialog_events import _attached_dialog_event_clients

                    _attached_dialog_event_clients.discard(_cid)
                except ImportError:
                    pass

    def start_pending(
        self, telegram_id: int, api_id: int, api_hash: str
    ) -> PendingLogin:
        client = TelegramClient(
            StringSession(),
            api_id,
            api_hash,
            proxy=parse_telethon_proxy(settings.proxy_url),
        )
        pending = PendingLogin(client=client, api_id=api_id, api_hash=api_hash)
        self._pending[telegram_id] = pending
        return pending

    def get_pending(self, telegram_id: int) -> PendingLogin | None:
        return self._pending.get(telegram_id)

    async def cancel_pending(self, telegram_id: int) -> None:
        pending = self._pending.pop(telegram_id, None)
        if pending is not None:
            try:
                await pending.client.disconnect()
            except RPCError:
                logger.exception("userbot disconnect failed")

    def clear_pending(self, telegram_id: int) -> PendingLogin | None:
        return self._pending.pop(telegram_id, None)

    async def shutdown(self) -> None:
        """Shutdown the manager — cancel retry tasks, disconnect all clients and pending logins."""
        logger.info("Shutting down UserbotManager")

        # 1. Cancel all pending FloodWait retry tasks
        for task in self._retry_tasks:
            task.cancel()
        if self._retry_tasks:
            await asyncio.gather(*self._retry_tasks, return_exceptions=True)
        self._retry_tasks.clear()

        # 2. Disconnect all active clients
        for tg_id, client in list(self._clients.items()):
            try:
                await client.disconnect()
            except RPCError:
                logger.exception("Error disconnecting client %s", tg_id)
        self._clients.clear()

        # 3. Cancel all pending logins
        for tg_id, pending in list(self._pending.items()):
            try:
                await pending.client.disconnect()
            except RPCError:
                logger.exception("Error disconnecting pending client %s", tg_id)
        self._pending.clear()

        logger.info("UserbotManager shutdown complete")


# ── Gateway registration (Protocol from core layer) ──────────────────────


class _UserbotGatewayImpl:
    """Implements UserbotGateway protocol — bridges core ↔ userbot layer."""

    def get_client(self, telegram_id: int):
        """Return active Telethon client for user, or None."""
        mgr = _MANAGER_SINGLETON
        return mgr.get_client(telegram_id) if mgr else None

    async def sync_dialogs(self, client, user, *, limit=500):
        """Sync Telegram dialogs to local DB."""
        from src.userbot.dialogs import sync_dialogs

        return await sync_dialogs(client, user, limit=limit)


from src.core.infra.userbot_gateway import set_userbot_gateway

set_userbot_gateway(_UserbotGatewayImpl())
