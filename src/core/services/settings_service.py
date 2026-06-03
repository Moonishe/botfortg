"""Settings service layer.

Provides a handler-friendly interface for reading and updating
:class:`UserSettings`, with strict validation of setting values.

No caching — callers (e.g. ``free_text_common._get_owner_context``)
own their own cache layer.

Delegates to:
  ``src.db.repos.session_repo`` — get_or_create_user (user lookup/creation)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import User, UserSettings
from src.db.repo import get_or_create_user
from .exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)


# ── Validator helpers ───────────────────────────────────────────────────


def _validate_bool(value: Any) -> bool:
    """Accept ``bool``, or string ``"true"``/``"false"``/``"1"``/``"0"``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.lower().strip()
        if lower in ("true", "1", "yes", "on"):
            return True
        if lower in ("false", "0", "no", "off"):
            return False
    raise ValidationError(f"Cannot coerce {value!r} to bool")


def _validate_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValidationError("Boolean is not a valid integer setting value")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise ValidationError(f"Cannot coerce {value!r} to int")


def _validate_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    raise ValidationError(f"Setting value must be a string, got {type(value).__name__}")


_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_hhmm(value: Any) -> str:
    """Validate ``HH:MM`` format."""
    s = _validate_str(value).strip()
    if not _TIME_RE.match(s):
        raise ValidationError(f"Time must be in HH:MM format (00:00-23:59), got {s!r}")
    return s


def _validate_timezone(value: Any) -> str:
    """Loose IANA timezone validation (accepts Area/Name or abbreviation)."""
    s = _validate_str(value).strip()
    if not s or len(s) > 64:
        raise ValidationError("Timezone string must be 1-64 characters")
    return s


def _validate_enum(*allowed: str):
    """Return a validator that rejects values outside ``allowed``."""

    def _v(value: Any) -> str:
        s = _validate_str(value).lower().strip()
        if s not in allowed:
            raise ValidationError(f"Value must be one of {list(allowed)}, got {s!r}")
        return s

    return _v


# ── Known settings whitelist ────────────────────────────────────────────
_KNOWN_SETTINGS: dict[str, Any] = {
    "auto_reply_enabled": _validate_bool,
    "use_heavy_model": _validate_bool,
    "digest_enabled": _validate_bool,
    "reminders_enabled": _validate_bool,
    "reminder_overdue_enabled": _validate_bool,
    "news_enabled": _validate_bool,
    "auto_sync_enabled": _validate_bool,
    "auto_extract_memories": _validate_bool,
    "include_saved_messages": _validate_bool,
    "smart_digest_enabled": _validate_bool,
    "urgent_notify_enabled": _validate_bool,
    "draft_suggestions_enabled": _validate_bool,
    "draft_only_important": _validate_bool,
    "ignore_archived": _validate_bool,
    "auto_reply_close_contacts": _validate_bool,
    "notify_on_auto_reply": _validate_bool,
    "pattern_caching_enabled": _validate_bool,
    "monitor_only_selected_folders": _validate_bool,
    "anti_ai_enabled": _validate_bool,
    "auto_reply_cooldown_min": _validate_int,
    "reminder_lead_hours": _validate_int,
    "news_window_hours": _validate_int,
    "auto_sync_interval_sec": _validate_int,
    "smart_digest_interval_min": _validate_int,
    "draft_max_per_hour": _validate_int,
    "digest_time": _validate_hhmm,
    "news_digest_time": _validate_hhmm,
    "quiet_hours_start": _validate_hhmm,
    "quiet_hours_end": _validate_hhmm,
    "timezone": _validate_timezone,
    "auto_reply_text": _validate_str,
    "model_overrides": _validate_str,
    "monitored_folders": _validate_str,
    "watched_peers": _validate_str,
    "vision_model": _validate_str,
    "auto_reply_mode": _validate_enum("static", "smart"),
    "auto_mode": _validate_enum("offline_only", "always", "smart"),
    "transcription_mode": _validate_enum("local", "api", "hybrid"),
    "transcription_api_provider": _validate_enum("openai", "gemini", "mistral"),
    "llm_provider": _validate_enum(
        "openai",
        "anthropic",
        "gemini",
        "mistral",
        "openrouter",
        "deepseek",
        "groq",
        "together",
        "xai",
    ),
    "anti_ai_mode": _validate_enum("off", "log", "fix"),
}


def _settings_to_dict(settings: UserSettings) -> dict[str, Any]:
    """Convert a :class:`UserSettings` ORM instance to a plain dict."""
    return {
        col.key: getattr(settings, col.key)
        for col in UserSettings.__table__.columns
        if col.key != "user_id"
    }


async def _load_user_with_settings(session: AsyncSession, user_id: int) -> User:
    """Load user with settings eagerly; raise :class:`NotFoundError` if missing."""
    from sqlalchemy import select

    result = await session.execute(
        select(User).where(User.id == user_id).options(selectinload(User.settings))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError(f"User with id={user_id} not found")
    return user


async def _ensure_settings(user: User) -> UserSettings:
    """Ensure the user has a :class:`UserSettings` row, creating one if needed."""
    if user.settings is not None:
        return user.settings
    settings = UserSettings(user_id=user.id)
    user.settings = settings
    return settings


# ── Public API ───────────────────────────────────────────────────────────


async def get_user_settings(
    session: AsyncSession,
    user_id: int,
) -> dict[str, Any]:
    """Get all user settings as a dictionary, directly from the database.

    Raises:
        NotFoundError: If the user does not exist.
    """
    user = await _load_user_with_settings(session, user_id)
    settings = await _ensure_settings(user)
    return _settings_to_dict(settings)


async def update_setting(
    session: AsyncSession,
    user_id: int,
    key: str,
    value: Any,
) -> Any:
    """Update a single user setting with validation.

    Raises:
        NotFoundError: If the user does not exist.
        ValidationError: If the setting name is unknown or value invalid.
    """
    if not isinstance(key, str) or not key.strip():
        raise ValidationError("Setting key must be a non-empty string")

    key = key.strip()
    validator = _KNOWN_SETTINGS.get(key)
    if validator is None:
        raise ValidationError(
            f"Unknown setting: {key!r}. Known: {sorted(_KNOWN_SETTINGS)}"
        )

    validated = validator(value)

    user = await _load_user_with_settings(session, user_id)
    settings = await _ensure_settings(user)

    setattr(settings, key, validated)
    await session.flush()

    logger.info("Updated setting %s=%r for user=%d", key, validated, user_id)
    return validated


async def reset_settings(
    session: AsyncSession,
    user_id: int,
) -> dict[str, Any]:
    """Reset all user settings to their model defaults.

    Raises:
        NotFoundError: If the user does not exist.
    """
    user = await _load_user_with_settings(session, user_id)

    if user.settings is not None:
        await session.delete(user.settings)
        await session.flush()

    new_settings = UserSettings(user_id=user.id)
    session.add(new_settings)
    user.settings = new_settings
    await session.flush()

    data = _settings_to_dict(new_settings)
    logger.info("Reset all settings to defaults for user=%d", user_id)
    return data


__all__ = [
    "get_user_settings",
    "update_setting",
    "reset_settings",
    "get_or_create_user",
]
