"""Constants, key mappings and validation helpers for settings.

SPR: constants & validation only — no router, no DB, no rendering.
"""

SEARCHABLE_SETTINGS: dict[str, str] = {
    # Раздел: Часовой пояс
    "timezone": "Часовой пояс (IANA, напр. Europe/Moscow)",
    # Раздел: Авто-ответ
    "auto_reply_enabled": "Включить авто-ответ при оффлайн",
    "auto_reply_cooldown_min": "Кулдаун между авто-ответами (мин)",
    "auto_reply_mode": "Режим авто-ответа (заготовка/умный)",
    "auto_reply_text": "Текст заготовки для авто-ответа",
    "auto_reply_close_contacts": "Авто-ответ только близким контактам",
    "notify_on_auto_reply": "Уведомлять об отправленных авто-ответах",
    # Раздел: Авто-режим
    "auto_mode": "Режим работы (оффлайн/всегда/умный)",
    "quiet_hours_start": "Начало тихих часов",
    "quiet_hours_end": "Конец тихих часов",
    # Раздел: Дайджест
    "digest_enabled": "Включить утренний дайджест",
    "digest_time": "Время отправки дайджеста (UTC)",
    # Раздел: Напоминания
    "reminders_enabled": "Включить напоминания о дедлайнах",
    "reminder_lead_hours": "За сколько часов напоминать о дедлайне",
    "reminder_overdue_enabled": "Алерт при просрочке дедлайна",
    # Раздел: Smart-дайджест
    "smart_digest_enabled": "Включить smart дайджест",
    "smart_digest_interval_min": "Интервал smart дайджеста (мин)",
    "urgent_notify_enabled": "Мгновенные уведомления о срочных сообщениях",
    # Раздел: Новости
    "news_enabled": "Включить авто-новости",
    "news_digest_time": "Время отправки авто-новостей",
    "news_window_hours": "Окно поиска новостей (ч)",
    # Раздел: LLM
    "llm_provider": "LLM-провайдер (openai/gemini/mistral/cloudflare/openrouter)",
    "use_heavy_model": "Использовать тяжёлую модель LLM",
    # Раздел: Транскрипция
    "transcription_mode": "Режим транскрипции (local/api/hybrid)",
    "transcription_api_provider": "API-провайдер транскрипции",
    # Раздел: Черновики
    "draft_suggestions_enabled": "Включить авто-черновики ответов",
    "draft_only_important": "Черновики только для важных сообщений",
    "draft_max_per_hour": "Максимум черновиков в час",
    # Раздел: Приватность
    "ignore_archived": "Игнорировать архивные чаты",
    # Раздел: Синхронизация
    "auto_sync_enabled": "Включить авто-синхронизацию",
    "auto_sync_interval_sec": "Интервал авто-синхронизации (сек)",
    "auto_extract_memories": "Авто-извлечение памяти из переписок",
    "include_saved_messages": "Индексировать Избранное (Saved Messages)",
    # Раздел: API-ключи
    "openai_key": "API ключ OpenAI",
    "gemini_key": "API ключ Gemini",
    "mistral_key": "API ключ Mistral",
    "cloudflare_key": "API ключ Cloudflare",
    "deepseek_key": "API ключ DeepSeek",
    # Раздел: Модели
    "model_overrides": "Переопределения моделей по типу задач",
    # Раздел: Папки
    "monitored_folders": "Отслеживаемые папки Telegram",
    "monitor_only_selected_folders": "Мониторить только выбранные папки",
    # Раздел: Личность
    "alias": "Псевдоним (обращение к владельцу)",
    "custom_instructions": "Пользовательские инструкции для личности",
    "base_tone": "Базовый тон личности",
    "warmth": "Теплота общения (low/normal/high)",
    "enthusiasm": "Восторженность (low/normal/high)",
    "headings_lists": "Заголовки и списки (low/normal/high)",
    "emoji_level": "Уровень использования эмодзи (low/normal/high)",
    "adaptive_mode_enabled": "Адаптивный режим личности",
    # Anti-AI
    "anti_ai_enabled": "Включить Anti-AI защиту",
    "anti_ai_mode": "Режим Anti-AI (off/log/fix)",
    "pattern_caching_enabled": "кэширование паттернов намерений",
}

BOOL_KEYS = {
    "auto_reply_enabled",
    "ignore_archived",
    "digest_enabled",
    "reminders_enabled",
    "reminder_overdue_enabled",
    "news_enabled",
    "use_heavy_model",
    "auto_sync_enabled",
    "auto_extract_memories",
    "include_saved_messages",
    "draft_suggestions_enabled",
    "draft_only_important",
    "smart_digest_enabled",
    "urgent_notify_enabled",
    "monitor_only_selected_folders",
    "auto_reply_close_contacts",
    "notify_on_auto_reply",
    "adaptive_mode_enabled",
    "anti_ai_enabled",
    "pattern_caching_enabled",
}

CHOICE_KEYS = {
    "llm_provider": {
        "openrouter",
        "openai",
        "gemini",
        "mistral",
        "cloudflare",
        "deepseek",
        "grok",
        "mimo",
        "groq",
        "custom",
    },
    "transcription_mode": {"local", "api", "hybrid"},
    "transcription_api_provider": {
        "openai",
        "gemini",
        "mistral",
        "deepgram",
        "assemblyai",
    },
    "auto_reply_mode": {"static", "smart"},
    "auto_mode": {"offline_only", "always", "smart"},
    # Личность (ChatGPT-style)
    "base_tone": {
        "default",
        "professional",
        "friendly",
        "frank",
        "whimsical",
        "efficient",
        "cynical",
    },
    "warmth": {"low", "normal", "high"},
    "enthusiasm": {"low", "normal", "high"},
    "headings_lists": {"low", "normal", "high"},
    "emoji_level": {"low", "normal", "high"},
    "anti_ai_mode": {"off", "log", "fix"},
}

NUMERIC_KEYS = {
    "auto_reply_cooldown_min",
    "reminder_lead_hours",
    "news_window_hours",
    "auto_sync_interval_sec",
    "draft_max_per_hour",
    "smart_digest_interval_min",
}

# Ключи, которые относятся к AdaptivePersona (не к owner.settings)
PERSONA_KEYS = frozenset(
    {
        "base_tone",
        "warmth",
        "enthusiasm",
        "headings_lists",
        "emoji_level",
        "adaptive_mode_enabled",
    }
)


def section_for_key(key: str) -> str:
    """Return settings section name for a given setting key."""
    return {
        "auto_reply_enabled": "auto_reply",
        "auto_reply_cooldown_min": "auto_reply",
        "auto_reply_mode": "auto_reply",
        "auto_reply_text": "auto_reply",
        "ignore_archived": "privacy",
        "digest_enabled": "digest",
        "reminders_enabled": "reminders",
        "reminder_lead_hours": "reminders",
        "reminder_overdue_enabled": "reminders",
        "news_enabled": "news",
        "news_window_hours": "news",
        "llm_provider": "brain",
        "use_heavy_model": "brain",
        "transcription_mode": "brain",
        "transcription_api_provider": "brain",
        "draft_suggestions_enabled": "drafts",
        "draft_only_important": "drafts",
        "draft_max_per_hour": "drafts",
        "auto_mode": "auto_mode",
        "auto_reply_close_contacts": "auto_mode",
        "notify_on_auto_reply": "auto_mode",
        "base_tone": "personality",
        "warmth": "personality",
        "enthusiasm": "personality",
        "headings_lists": "personality",
        "emoji_level": "personality",
        "adaptive_mode_enabled": "personality",
        "anti_ai_enabled": "personality",
        "anti_ai_mode": "personality",
        "monitor_only_selected_folders": "privacy",
        "auto_sync_enabled": "sync",
        "auto_extract_memories": "sync",
        "include_saved_messages": "sync",
        "smart_digest_enabled": "smart_digest",
        "urgent_notify_enabled": "smart_digest",
        "smart_digest_interval_min": "smart_digest",
        "auto_sync_interval_sec": "sync",
        "pattern_caching_enabled": "brain",
    }.get(key, "menu")


def validate_model_name(model_name: str) -> bool:
    """Validate model name format: letters, digits, @ / _ . : -"""
    import re

    return bool(re.match(r"^[\w@/_.:-]+$", model_name)) if model_name else False
