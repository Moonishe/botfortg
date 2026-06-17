import logging
from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from src.config import settings
from src.core.onboarding import get_onboarding_phase, is_onboarded  # re-export
from src.core.security.pairing import pairing

__all__ = [
    "OwnerOnly",
    "OwnerOnlyStrict",
    "get_onboarding_phase",
    "is_onboarded",
]

logger = logging.getLogger(__name__)


class OwnerOnlyStrict(BaseFilter):
    """Допускает только владельца (не paired-контакты)."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if event.from_user is None:
            return False
        owner_id = settings.owner_telegram_id
        if owner_id <= 0:
            logger.critical(
                "OWNER_TELEGRAM_ID is %d (not set or invalid). "
                "REJECTING ALL USERS for safety.",
                owner_id,
            )
            return False
        return event.from_user.id == owner_id


class OwnerOnly(BaseFilter):
    """Допускает владельца и approved paired-контакты.

    Если OWNER_TELEGRAM_ID == 0 (не задан / значение по умолчанию),
    фильтр отклоняет ВСЕХ пользователей и пишет CRITICAL в лог —
    защита от случайного открытия доступа всем.
    """

    async def __call__(self, event: Message | CallbackQuery, **kwargs: Any) -> bool:
        # Канальные посты приходят с from_user=None — явно отклоняем
        if event.from_user is None:
            return False
        # Paired users are marked by the pairing guard middleware
        # for messages/callbacks. For other update types (e.g., inline
        # queries) that bypass the middleware, check pairing directly.
        if kwargs.get("_paired_user"):
            return True
        try:
            if await pairing.is_allowed(event.from_user.id):
                return True
        except Exception:
            logger.debug(
                "OwnerOnly: pairing.is_allowed fallback failed for user %d",
                event.from_user.id,
                exc_info=True,
            )
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
