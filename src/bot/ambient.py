"""Ambient Intelligence — проактивные уведомления на основе изменений контекста.

Отвечает за:
- Утренний брифинг (первое сообщение дня)
- Проактивные нотификации при изменении контекста
- Форматирование и отправку через Telegram-бота

Включается/выключается флагом ambient_intelligence_enabled в config.
"""

from __future__ import annotations

import logging
from datetime import datetime, UTC

logger = logging.getLogger(__name__)


class AmbientIntelligence:
    """Проактивные уведомления на основе контекстных изменений."""

    def __init__(self, bot) -> None:
        """Инициализация с экземпляром Telegram-бота.

        Args:
            bot: Экземпляр aiogram Bot для отправки сообщений.
        """
        self._bot = bot

    async def check_and_notify(self, user_id: int, context: dict) -> None:
        """Проверить контекст и отправить уведомления при необходимости.

        Вызывается при старте сессии или после длительного перерыва.
        Проверяет:
        - Первое ли это сообщение за сегодня (→ утренний брифинг)
        - Другие триггеры (будущее расширение)

        Args:
            user_id: Telegram user_id владельца.
            context: Словарь с ключами:
                - last_active_at (datetime | None)
                - active_tasks (list[str])
                - recent_insights (list[str])
        """
        if self._is_first_message_today(context):
            await self._send_morning_briefing(user_id, context)
        else:
            logger.debug(
                "AmbientIntelligence: user=%s — не первое сообщение, брифинг пропущен",
                user_id,
            )

    async def _send_morning_briefing(self, user_id: int, context: dict) -> None:
        """Отправить утренний брифинг с повесткой дня.

        Формирует сообщение из активных задач и недавних инсайтов,
        отправляет владельцу через бота.

        Args:
            user_id: Telegram user_id владельца.
            context: Словарь с active_tasks и recent_insights.
        """
        from src.bot.tg_sender import safe_send

        tasks = context.get("active_tasks", [])
        insights = context.get("recent_insights", [])

        parts: list[str] = ["🌅 <b>Доброе утро!</b>\n"]

        if tasks:
            parts.append("📋 <b>Активные задачи:</b>")
            for i, task in enumerate(tasks, 1):
                parts.append(f"  {i}. {task}")
            parts.append("")

        if insights:
            parts.append("💡 <b>Недавние инсайты:</b>")
            for insight in insights[:5]:
                parts.append(f"  • {insight}")
            parts.append("")

        if not tasks and not insights:
            parts.append("✨ Чистый день — отличное время для новых идей!")

        text = "\n".join(parts)
        try:
            await safe_send(self._bot, chat_id=user_id, text=text)
            logger.info(
                "AmbientIntelligence: утренний брифинг отправлен user=%s (tasks=%d, insights=%d)",
                user_id,
                len(tasks),
                len(insights),
            )
        except Exception:
            logger.exception(
                "AmbientIntelligence: ошибка отправки утреннего брифинга user=%s",
                user_id,
            )

    @staticmethod
    def _is_first_message_today(context: dict) -> bool:
        """Проверить, первое ли это сообщение пользователя за сегодня.

        Args:
            context: Словарь с ключом last_active_at (datetime | None).

        Returns:
            True если last_active_at отсутствует или его дата < сегодня.
        """
        last_active = context.get("last_active_at")
        if last_active is None:
            return True  # Первое сообщение — считаем новым днём
        now = datetime.now(UTC)
        # NOTE: UTC date comparison — ignores user timezone.
        # Acceptable tradeoff for single-user bot; multi-user would need
        # per-user timezone-aware "start of day" logic.
        return last_active.date() < now.date()
