"""Onboarding phase detection — shared between bot and userbot layers.

The implementation previously lived in ``src.bot.filters`` and was imported
by ``src.userbot.mirror`` (a layering violation: userbot/core must not
import from bot/). It has been moved here so that both the bot and
userbot layers can call it without crossing boundaries.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.db.models import User
from src.db.session import get_session

__all__ = ["get_onboarding_phase", "is_onboarded"]


async def _load_owner(tg_id: int) -> User | None:
    async with get_session() as session:
        stmt = (
            select(User)
            .where(User.telegram_id == tg_id)
            .options(
                selectinload(User.session),
                selectinload(User.key_slots),
                selectinload(User.settings),
            )
        )
        return (await session.execute(stmt)).unique().scalar_one_or_none()


async def is_onboarded(tg_id: int) -> bool:
    """Проверяет, прошёл ли пользователь полный онбординг.

    Критерии:
      - есть активная Telegram-сессия
      - есть хотя бы один LLM-ключ (LlmKeySlot)
      - часовой пояс отличается от UTC (или "Europe/Moscow" и т.п.)
    """
    owner = await _load_owner(tg_id)
    if owner is None:
        return False
    has_session = owner.session is not None
    has_llm_key = len(owner.key_slots) > 0
    has_tz = owner.settings.timezone not in (None, "", "UTC", "Etc/UTC")
    return has_session and has_llm_key and has_tz


async def get_onboarding_phase(tg_id: int) -> int:
    """Возвращает фазу онбординга (1–4).

    Фазы:
      1 — нет Telegram-сессии (только /start, /login, /cancel)
      2 — нет LLM-ключа (плюс /keys add)
      3 — нет часового пояса / синхронизации (всё разрешено, но с подсказкой /sync)
      4 — онбординг завершён
    """
    owner = await _load_owner(tg_id)
    if owner is None:
        return 1  # незнакомец — фаза 1

    has_session = owner.session is not None
    has_llm_key = len(owner.key_slots) > 0
    has_tz = owner.settings.timezone not in (None, "", "UTC", "Etc/UTC")

    if not has_session:
        return 1
    if not has_llm_key:
        return 2
    if not has_tz:
        return 3
    return 4
