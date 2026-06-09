import logging

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from src.config import settings
from src.core.onboarding import get_onboarding_phase, is_onboarded  # re-export

__all__ = ["OwnerOnly", "is_onboarded", "get_onboarding_phase"]

logger = logging.getLogger(__name__)


class OwnerOnly(BaseFilter):
    """Допускает только владельца, указанного в OWNER_TELEGRAM_ID.

    Если OWNER_TELEGRAM_ID == 0 (не задан / значение по умолчанию),
    фильтр отклоняет ВСЕХ пользователей и пишет CRITICAL в лог —
    защита от случайного открытия доступа всем.
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        # Канальные посты приходят с from_user=None — явно отклоняем
        if event.from_user is None:
            return False
        owner_id = settings.owner_telegram_id
        if owner_id <= 0:
            logger.critical(
                "OWNER_TELEGRAM_ID is %d (not set or invalid). "
                "REJECTING ALL USERS for safety. "
                "Set OWNER_TELEGRAM_ID in .env to your real Telegram user ID.",
                owner_id,
            )
            return False
        return event.from_user.id == owner_id
