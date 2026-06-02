from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from src.config import settings
from src.core.onboarding import get_onboarding_phase, is_onboarded  # re-export

__all__ = ["OwnerOnly", "is_onboarded", "get_onboarding_phase"]


class OwnerOnly(BaseFilter):
    """Допускает только владельца, указанного в OWNER_TELEGRAM_ID."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return user is not None and user.id == settings.owner_telegram_id
