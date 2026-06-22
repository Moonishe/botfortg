"""Business logic for settings — DB operations, export/import, helpers.

SRP: data access and business logic — no router, no rendering, no handlers.
"""

import json
import logging
from datetime import datetime, UTC

from src.db.repo import (
    get_or_create_user,
    list_key_slots,
)
from src.db.session import get_session

logger = logging.getLogger(__name__)

# P3: Whitelist of settings keys allowed for import — prevents mass assignment.
_ALLOWED_IMPORT_KEYS: frozenset[str] = frozenset(
    {
        "llm_provider",
        "use_heavy_model",
        "transcription_mode",
        "transcription_api_provider",
        "anti_ai_enabled",
        "anti_ai_mode",
        "adaptive_mode_enabled",
        "auto_sync_enabled",
        "auto_extract_memories",
        "include_saved_messages",
        "monitor_only_selected_folders",
        "monitored_folders",
        "timezone",
        "auto_reply_close_contacts",
        "smart_digest_enabled",
        "urgent_notify_enabled",
        "digest_time",
        "auto_sync_interval_sec",
    }
)


async def _count_slots_for_provider(session, owner, provider: str) -> int:
    """Сколько ключей у пользователя для данного провайдера в LlmKeySlot."""
    slots = await list_key_slots(session, owner, provider=provider)
    return len(slots)


async def _collect_export_config(telegram_id: int) -> dict:
    """Собрать конфигурацию пользователя для экспорта."""
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        s = owner.settings

        try:
            overrides = json.loads(s.model_overrides) if s.model_overrides else {}
        except (json.JSONDecodeError, TypeError):
            overrides = {}

        config = {
            "version": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "settings": {
                "llm_provider": s.llm_provider,
                "use_heavy_model": s.use_heavy_model,
                "transcription_mode": s.transcription_mode,
                "transcription_api_provider": getattr(
                    s, "transcription_api_provider", "openai"
                ),
                "anti_ai_enabled": getattr(s, "anti_ai_enabled", False),
                "anti_ai_mode": getattr(s, "anti_ai_mode", "off"),
                "adaptive_mode_enabled": getattr(s, "adaptive_mode_enabled", False),
                "auto_sync_enabled": getattr(s, "auto_sync_enabled", True),
                "auto_extract_memories": getattr(s, "auto_extract_memories", False),
                "include_saved_messages": getattr(s, "include_saved_messages", False),
                "monitor_only_selected_folders": getattr(
                    s, "monitor_only_selected_folders", False
                ),
                "monitored_folders": s.monitored_folders,
                "timezone": s.timezone,
                "auto_reply_close_contacts": getattr(
                    s, "auto_reply_close_contacts", False
                ),
                "smart_digest_enabled": getattr(s, "smart_digest_enabled", False),
                "urgent_notify_enabled": getattr(s, "urgent_notify_enabled", False),
                "digest_time": s.digest_time,
                "auto_sync_interval_sec": getattr(s, "auto_sync_interval_sec", 7200),
            },
            "model_overrides": overrides,
            "keys": [],
        }

        slots = await list_key_slots(session, owner)
        for slot in slots:
            if slot.enabled:
                config["keys"].append(
                    {
                        "provider": slot.provider,
                        "purpose": slot.purpose,
                        "model": slot.model,
                        "endpoint": slot.endpoint,
                        "category": slot.category,
                        "label": slot.label,
                        "priority": slot.priority,
                        "key_enc": slot.key_enc,
                    }
                )

        return config


async def _apply_import_config(telegram_id: int, config: dict) -> dict:
    """Применить импортированную конфигурацию к пользователю.

    Returns dict with counts: settings_count, keys_count, overrides_count.
    """
    from src.db.models._auth import LlmKeySlot

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        s = owner.settings

        settings_data = config.get("settings", {})
        for key, value in settings_data.items():
            if key in _ALLOWED_IMPORT_KEYS and hasattr(s, key) and value is not None:
                setattr(s, key, value)

        overrides = config.get("model_overrides", {})
        if overrides:
            s.model_overrides = json.dumps(overrides, ensure_ascii=False)

        imported_keys = config.get("keys", [])
        existing = await list_key_slots(session, owner)
        keys_count = 0

        for key_data in imported_keys:
            duplicate = False
            for existing_slot in existing:
                if existing_slot.provider == key_data[
                    "provider"
                ] and existing_slot.purpose == key_data.get("purpose", "main"):
                    duplicate = True
                    break
            if duplicate:
                continue

            slot = LlmKeySlot(
                user_id=owner.id,
                provider=key_data["provider"],
                purpose=key_data.get("purpose", "main"),
                model=key_data.get("model"),
                endpoint=key_data.get("endpoint"),
                category=key_data.get("category", "llm"),
                label=key_data.get("label"),
                priority=key_data.get("priority", 0),
                # NOTE: key_enc импортируется как есть, без перешифрования.
                # Если мастер-ключ изменился, старые ключи не будут расшифрованы.
                # Перешифрование всех ключей — отдельная операция (вне скоупа импорта).
                key_enc=key_data["key_enc"],
            )
            session.add(slot)
            keys_count += 1

        await session.commit()

    # Invalidate settings cache
    from src.core.infra.settings_cache import invalidate_settings_cache

    await invalidate_settings_cache(telegram_id)

    return {
        "settings_count": len(settings_data),
        "keys_count": keys_count,
        "overrides_count": len(overrides),
    }


async def _update_setting(telegram_id: int, key: str, value) -> None:
    """Update a single setting on the user's settings object."""
    from src.core.infra.settings_cache import invalidate_settings_cache

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        if hasattr(owner.settings, key):
            setattr(owner.settings, key, value)
        await session.commit()

    await invalidate_settings_cache(telegram_id)
