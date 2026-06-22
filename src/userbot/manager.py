import asyncio
import logging
import time
from dataclasses import dataclass, field

from cryptography.fernet import Fernet
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession

from src.config import parse_telethon_proxy, settings
from src.db.repo import load_telegram_session
from src.db.session import get_session


logger = logging.getLogger(__name__)


def _encrypt_sensitive(value: str) -> str:
    key = settings.encryption_key
    if not key:
        return value
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def _decrypt_sensitive(value: str | None) -> str | None:
    if value is None:
        return None
    key = settings.encryption_key
    if not key:
        return value
    return Fernet(key.encode()).decrypt(value.encode()).decode()


@dataclass
class PendingLogin:
    # Промежуточное состояние логина: между запросами кода и 2FA-пароля

    client: TelegramClient
    api_id: int
    api_hash: str
    phone: str | None = None
    _phone_code_hash: str | None = None
    created_at: float = field(default_factory=time.monotonic)

    @property
    def phone_code_hash(self) -> str | None:
        return _decrypt_sensitive(self._phone_code_hash)

    @phone_code_hash.setter
    def phone_code_hash(self, value: str | None) -> None:
        if value is None:
            self._phone_code_hash = None
        else:
            self._phone_code_hash = _encrypt_sensitive(value)


_MANAGER_SINGLETON: "UserbotManager | None" = None


@dataclass
class UserbotManager:
    _clients: dict[int, TelegramClient] = field(default_factory=dict)
    _clients_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _pending: dict[int, PendingLogin] = field(default_factory=dict)
    _retry_tasks: set[asyncio.Task] = field(default_factory=set)
    _restore_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _restored: bool = False
    _health_check_running: bool = False

    def __post_init__(self) -> None:
        global _MANAGER_SINGLETON
        if _MANAGER_SINGLETON is not None and _MANAGER_SINGLETON is not self:
            logger.error(
                "UserbotManager singleton already exists — overwriting "
                "dangles clients on the old instance. Use set_global_manager() "
                "or shutdown the old one first."
            )
        _MANAGER_SINGLETON = self

    async def restore_all(self) -> None:
        async with self._restore_lock:
            if self._restored:
                logger.warning("restore_all already called — skipping duplicate")
                return
            self._restored = True
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
                    connection_retries=5,
                    retry_delay=1,
                    auto_reconnect=True,
                    request_retries=3,
                )
                try:
                    await client.connect()
                    if await client.is_user_authorized():
                        async with self._clients_lock:
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
                    await asyncio.sleep(max(0.0, min(e.seconds, 300)))
                    await client.connect()
                    if await client.is_user_authorized():
                        async with self._clients_lock:
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
                        try:
                            await client.disconnect()
                        except (RPCError, ConnectionError, OSError):
                            logger.debug(
                                "restore_all: disconnect after failure also failed for %s",
                                user.telegram_id,
                                exc_info=True,
                            )

    def get_client(self, telegram_id: int) -> TelegramClient | None:
        # read-only dict.get() is atomic under CPython GIL;
        # caller is responsible for not using a disconnected client.
        return self._clients.get(telegram_id)

    async def register_client(self, telegram_id: int, client: TelegramClient) -> None:
        async with self._clients_lock:
            self._clients[telegram_id] = client
        from src.userbot.auto_reply import attach_auto_reply
        from src.userbot.dialog_events import attach_dialog_event_handlers
        from src.userbot.mirror import attach_mirror

        attach_auto_reply(client, telegram_id)
        attach_dialog_event_handlers(client, telegram_id)
        attach_mirror(client, telegram_id)

    async def remove_client(self, telegram_id: int, *, permanent: bool = False) -> None:
        async with self._clients_lock:
            client = self._clients.pop(telegram_id, None)
        if client is not None:
            if permanent:
                try:
                    await client.log_out()
                except (RPCError, ConnectionError, OSError):
                    logger.exception("log_out failed during permanent removal")
            try:
                await client.disconnect()
            except (RPCError, ConnectionError, OSError):
                logger.exception("userbot disconnect failed")
            finally:
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
            connection_retries=3,
            retry_delay=1,
            auto_reconnect=True,
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
        """Shutdown: cancel retry tasks, disconnect clients and pending logins.

        Holds the restore lock for the entire duration so that no concurrent
        restore_all() can add clients while we are disconnecting them.
        """
        logger.info("Shutting down UserbotManager")

        async with self._restore_lock:
            # 1. Cancel all pending FloodWait retry tasks
            for task in self._retry_tasks:
                task.cancel()
            if self._retry_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*self._retry_tasks, return_exceptions=True),
                        timeout=30.0,
                    )
                except TimeoutError:
                    logger.warning(
                        "Timeout waiting for retry tasks to finish during shutdown"
                    )
            self._retry_tasks.clear()

            # 2. Disconnect all active clients
            async with self._clients_lock:
                clients_snapshot = list(self._clients.items())
            for tg_id, client in clients_snapshot:
                try:
                    await client.disconnect()
                except (RPCError, ConnectionError, OSError):
                    logger.exception("Error disconnecting client %s", tg_id)
            async with self._clients_lock:
                self._clients.clear()

            # 3. Cancel all pending logins
            for tg_id, pending in list(self._pending.items()):
                try:
                    await pending.client.disconnect()
                except (RPCError, ConnectionError, OSError):
                    logger.exception("Error disconnecting pending client %s", tg_id)
            self._pending.clear()
            self._restored = False

        logger.info("UserbotManager shutdown complete")

    _PENDING_LOGIN_TTL = 600  # 10 minutes

    async def cleanup_stale_pending(self) -> None:
        """Disconnect pending login clients older than TTL."""
        now = time.monotonic()
        stale = []
        for tg_id, pending in self._pending.items():
            if now - pending.created_at > self._PENDING_LOGIN_TTL:
                stale.append(tg_id)
        for tg_id in stale:
            pending = self._pending.pop(tg_id, None)
            if pending is not None:
                try:
                    await pending.client.disconnect()
                    logger.info(
                        "Cleaned up stale pending login for tg_id=%d (age=%.0fs)",
                        tg_id,
                        now - pending.created_at,
                    )
                except (RPCError, ConnectionError, OSError):
                    logger.exception(
                        "Failed to disconnect stale pending client %d", tg_id
                    )

    async def health_check_loop(self) -> None:
        """Periodically check connected + authorized for all userbot clients."""
        if self._health_check_running:
            logger.debug("health_check_loop already running, skipping")
            return
        self._health_check_running = True
        try:
            while True:
                await asyncio.sleep(settings.userbot_health_check_interval)
                # Snapshot under lock to prevent dict mutation during iteration.
                async with self._clients_lock:
                    snapshot = list(self._clients.items())
                for tg_id, client in snapshot:
                    try:
                        if not client.is_connected():
                            logger.warning(
                                "Userbot for tg_id=%d disconnected — reconnecting…",
                                tg_id,
                            )
                            await client.connect()
                        me = await client.get_me()
                        if me is None:
                            logger.error(
                                "Userbot for tg_id=%d not authorized — removing",
                                tg_id,
                            )
                            try:
                                await client.disconnect()
                            except RPCError:
                                logger.debug(
                                    "client.disconnect() failed for tg_id=%d",
                                    tg_id,
                                    exc_info=True,
                                )
                            # Atomic re-check: only remove if still the same client
                            # (another coroutine may have replaced/removed it).
                            async with self._clients_lock:
                                if self._clients.get(tg_id) is client:
                                    self._clients.pop(tg_id, None)
                    except asyncio.CancelledError:
                        raise  # propagate for clean shutdown
                    except FloodWaitError as e:
                        logger.warning(
                            "FloodWait %ds in health_check for user %s, sleeping...",
                            e.seconds,
                            tg_id,
                        )
                        await asyncio.sleep(max(0.0, min(e.seconds, 300)))
                        continue  # retry on next tick, don't disconnect
                    except (RPCError, ConnectionError, OSError):
                        # Reconnect failed — disconnect to release resources
                        logger.warning(
                            "Health check reconnect failed for tg_id=%d — disconnecting",
                            tg_id,
                            exc_info=True,
                        )
                        try:
                            await client.disconnect()
                        except (RPCError, OSError):
                            logger.debug(
                                "client.disconnect() after failed reconnect for tg_id=%d",
                                tg_id,
                                exc_info=True,
                            )
                        async with self._clients_lock:
                            if self._clients.get(tg_id) is client:
                                self._clients.pop(tg_id, None)
                    except Exception:
                        logger.debug(
                            "Health check for tg_id=%d failed (non-critical)",
                            tg_id,
                            exc_info=True,
                        )
        finally:
            self._health_check_running = False


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
