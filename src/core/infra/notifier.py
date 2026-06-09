# NOTE: Для некритических уведомлений используй notification_queue.enqueue()
# вместо notifier.notify(). Прямой вызов notifier.notify() — только для CRITICAL.
import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING

from src.config import settings
from src.core.infra._retry import send_with_retry
from src.core.infra.task_manager import track_ff


if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import InlineKeyboardMarkup


logger = logging.getLogger(__name__)


class Notifier:
    # шлёт сообщения владельцу через control bot — используется userbot-кодом

    def __init__(self) -> None:
        self._bot: "Bot | None" = None
        self._buffer: deque[dict] = deque()

    def attach(self, bot: "Bot") -> None:
        self._bot = bot
        # Flush buffered notifications
        if self._buffer:
            logger.info("Flushing %d buffered notifications", len(self._buffer))
            track_ff(asyncio.create_task(self._flush_buffer()))

    async def _flush_buffer(self) -> None:
        while self._buffer:
            item = self._buffer.popleft()
            try:
                await send_with_retry(
                    self._bot.send_message,
                    chat_id=settings.owner_telegram_id,
                    **item,
                )
            except Exception:
                logger.exception("Failed to send buffered notification")

    async def notify(
        self,
        text: str,
        *,
        parse_mode: str | None = "HTML",
        reply_markup: "InlineKeyboardMarkup | None" = None,
    ) -> None:
        if self._bot is None:
            logger.warning("Notifier not yet attached, buffering: %s", text[:80])
            self._buffer.append(
                {"text": text, "parse_mode": parse_mode, "reply_markup": reply_markup}
            )
            return
        try:
            await send_with_retry(
                self._bot.send_message,
                chat_id=settings.owner_telegram_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        except Exception:
            logger.exception("Failed to notify owner after retries")


notifier = Notifier()
