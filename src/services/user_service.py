"""User service — операции с пользователем (get/create/update)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from src.db.models._auth import UserSettings
from src.db.models._base import User
from src.db.repo import get_or_create_user as _repo_get_or_create_user
from src.db.session import get_session

logger = logging.getLogger(__name__)


# ── public API ─────────────────────────────────────────────────────────────────


async def get_or_create_user(telegram_id: int) -> dict[str, Any] | None:
    """Получить или создать пользователя.

    Возвращает словарь с ключами:
      - id: int              — первичный ключ в БД
      - telegram_id: int     — Telegram ID пользователя
      - created_at: datetime — дата создания записи
      - has_session: bool    — подключена ли Telegram-сессия
      - settings: dict       — выбранные поля UserSettings (см. _settings_to_dict)

    Возвращает None при ошибке БД (логируется внутри).
    Не-SQLAlchemy исключения пробрасываются наверх.
    """
    try:
        async with get_session() as session:
            user = await _repo_get_or_create_user(session, telegram_id)
            return _user_to_dict(user)
    except SQLAlchemyError:
        logger.exception("get_or_create_user failed for telegram_id=%d", telegram_id)
        return None


async def get_user_settings(telegram_id: int) -> dict[str, Any] | None:
    """Получить настройки пользователя.

    Возвращает словарь с полями UserSettings или None при ошибке.
    """
    try:
        async with get_session() as session:
            user = await _repo_get_or_create_user(session, telegram_id)
            return _settings_to_dict(user.settings) if user.settings else {}
    except SQLAlchemyError:
        logger.exception("get_user_settings failed for telegram_id=%d", telegram_id)
        return None


async def update_user_settings(telegram_id: int, **kwargs: object) -> bool:
    """Обновить настройки пользователя.

    Принимает именованные аргументы, соответствующие полям UserSettings.
    Возвращает True при успехе, False при ошибке.
    """
    if not kwargs:
        return False
    try:
        async with get_session() as session:
            user = await _repo_get_or_create_user(session, telegram_id)
            if user.settings is None:
                logger.error(
                    "update_user_settings: user.settings is None for telegram_id=%d",
                    telegram_id,
                )
                return False
            changed = False
            for key, value in kwargs.items():
                if hasattr(user.settings, key):
                    setattr(user.settings, key, value)
                    changed = True
                else:
                    logger.warning(
                        "update_user_settings: unknown key %s for telegram_id=%d — "
                        "not a UserSettings attribute, mass assignment ignored",
                        key,
                        telegram_id,
                    )
            # get_session делает commit при выходе из async with
            return changed
    except SQLAlchemyError:
        logger.exception(
            "update_user_settings failed for telegram_id=%d, kwargs=%s",
            telegram_id,
            kwargs,
        )
        return False


# ── internal helpers ──────────────────────────────────────────────────────────


def _user_to_dict(user: User) -> dict[str, Any]:
    """Преобразует User ORM-объект в словарь."""
    settings: dict[str, Any] = {}
    if user.settings is not None:
        settings = _settings_to_dict(user.settings)
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "created_at": user.created_at
        if isinstance(user.created_at, datetime)
        else None,
        "has_session": user.session is not None,
        "settings": settings,
    }


def _settings_to_dict(settings: UserSettings) -> dict[str, Any]:
    """Выбрать ключевые поля UserSettings в dict."""
    fields = (
        "llm_provider",
        "timezone",
        "digest_time",
        "digest_enabled",
        "auto_reply_enabled",
        "auto_reply_mode",
        "auto_reply_text",
        "auto_reply_cooldown_min",
        "use_heavy_model",
        "news_enabled",
        "news_window_hours",
        "news_digest_time",
        "reminders_enabled",
        "auto_sync_enabled",
        "auto_sync_interval_sec",
        "monitored_folders",
        "monitor_only_selected_folders",
    )
    result: dict[str, Any] = {}
    for f in fields:
        if hasattr(settings, f):
            result[f] = getattr(settings, f)
    return result
