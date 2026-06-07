from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_telethon_proxy(proxy_url: str) -> tuple | None:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme or "socks5"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (1080 if scheme == "socks5" else 8080)
    if parsed.username and parsed.password:
        return (scheme, host, port, True, parsed.username, parsed.password)
    return (scheme, host, port)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Empty string in .env → None for optional fields.
    # Prevents ValidationError when .env has ``API_ID=`` (empty)
    # instead of the line being absent entirely.
    @field_validator("api_id", "api_hash", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @field_validator("encryption_key", mode="after")
    @classmethod
    def _validate_encryption_key(cls, v: str) -> str:
        """Validate Fernet key format at config load time."""
        if not v or len(v) != 44:
            raise ValueError(
                "ENCRYPTION_KEY must be exactly 44 characters (32-byte key encoded as urlsafe-base64). "
                "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        try:
            from cryptography.fernet import Fernet

            Fernet(v.encode())
        except Exception as e:
            raise ValueError(f"ENCRYPTION_KEY is not a valid Fernet key: {e}") from e
        return v

    @field_validator("owner_telegram_id", mode="after")
    @classmethod
    def _validate_owner_id(cls, v: int) -> int:
        """Telegram user IDs are always positive."""
        if v <= 0:
            raise ValueError(
                f"OWNER_TELEGRAM_ID must be positive, got {v}. "
                "Get your ID from @userinfobot"
            )
        return v

    @field_validator("bot_token", mode="after")
    @classmethod
    def _validate_bot_token(cls, v: str) -> str:
        """Validate Telegram bot token format."""
        import re

        if not v:
            raise ValueError("BOT_TOKEN cannot be empty")
        if not re.match(r"^\d{8,13}:[A-Za-z0-9_-]{30,50}$", v):
            raise ValueError(
                "BOT_TOKEN format invalid. Expected: {bot_id}:{token}. "
                "Get token from @BotFather"
            )
        return v

    bot_token: str = Field(..., description="Токен control-бота из @BotFather")
    owner_telegram_id: int = Field(
        ..., description="Telegram user_id единственного владельца"
    )
    encryption_key: str = Field(..., description="Fernet-ключ (base64)")
    database_url: str = Field("sqlite+aiosqlite:///data/app.db")
    proxy_url: str = Field(
        "",
        description="Прокси для aiogram и Telethon (socks5://ip:port или http://ip:port)",
    )
    disable_local_transcription: bool = Field(
        False, description="Полностью отключить faster-whisper (для VDS с малым RAM)"
    )

    # --- Интервалы фоновых циклов (секунды) ---
    global_style_interval_sec: int = Field(
        12 * 3600, description="Интервал обновления глобального стиля"
    )
    instruction_optimizer_interval_sec: int = Field(
        24 * 3600, description="Интервал цикла оптимизатора инструкций"
    )
    skill_optimizer_interval_sec: int = Field(
        24 * 3600, description="Интервал цикла оптимизатора навыков"
    )
    weekly_digest_check_sec: int = Field(
        3600, description="Проверка еженедельного дайджеста"
    )
    weekly_summary_check_sec: int = Field(
        3600, description="Проверка еженедельного саммари"
    )
    conflict_predictor_interval_sec: int = Field(
        3 * 3600, description="Интервал предсказания конфликтов"
    )
    follow_up_interval_sec: int = Field(
        4 * 3600, description="Интервал follow-up напоминаний"
    )
    memory_clusterer_interval_sec: int = Field(
        600, description="Интервал кластеризации памяти"
    )
    temporal_migration_interval_sec: int = Field(
        3600, description="Интервал миграции временных слоёв"
    )
    habit_tracker_interval_sec: int = Field(
        3600, description="Интервал трекера привычек"
    )
    # ── Prefetch recall ──
    prefetch_recall_enabled: bool = Field(
        True, description="Включить оптимистичный prefetch memory recall (S1-T1)"
    )
    prefetch_recall_ttl: float = Field(
        5.0, description="TTL кэша prefetch recall (секунды)"
    )

    memory_check_interval_sec: int = Field(600, description="Интервал проверки памяти")
    auto_sync_interval_sec: int = Field(
        3600, description="Интервал авто-синхронизации контактов"
    )
    auto_sync_fallback_sec: int = Field(
        300, description="Fallback-интервал при ошибке синхронизации"
    )
    digest_check_sec: int = Field(60, description="Интервал проверки дайджеста")
    news_check_sec: int = Field(60, description="Интервал проверки новостей")
    avito_check_sec: int = Field(1800, description="Интервал проверки Авито (сек)")
    avito_default_city: str = Field(
        "moskva", description="Город по умолчанию для Авито"
    )
    avito_proxy_list: str = Field(
        "",
        description='JSON-список прокси: [{"url":"...","type":"mobile","change_ip_url":"..."}]',
    )
    avito_fetch_details: bool = Field(
        False, description="Загружать полные описания с карточек объявлений"
    )
    avito_detail_fetch_limit: int = Field(
        10, description="Максимум карточек для загрузки полных описаний за один скан"
    )
    avito_llm_analysis: bool = Field(
        False,
        description="Анализировать объявления через LLM (требует полные описания)",
    )
    sleep_tracker_check_sec: int = Field(900, description="Интервал трекера сна")
    sleep_tracker_fallback_sec: int = Field(600, description="Fallback трекера сна")
    memory_patterns_interval_sec: int = Field(
        600, description="Интервал поиска паттернов памяти"
    )
    proactive_briefing_check_sec: int = Field(
        300, description="Интервал проактивного брифинга"
    )
    conflict_resolver_interval_sec: int = Field(
        600, description="Интервал разрешения конфликтов"
    )
    knowledge_distiller_interval_sec: int = Field(
        600, description="Интервал дистилляции знаний"
    )

    # --- Cloudflare Workers AI ---
    openai_base_url: str = Field(
        "",
        description="Кастомный base_url для OpenAI-совместимых API (например, https://macky1.icu/v1). Оставь пустым для стандартного OpenAI.",
    )

    cloudflare_account_id: str = Field(
        "", description="Cloudflare Account ID (из URL дашборда)"
    )

    context7_api_key: str = Field(
        "",
        description="Context7 API key for documentation search (https://context7.com)",
    )

    embedding_dim: int = Field(
        1536,
        description="Размерность эмбеддингов (OpenAI text-embedding-3-small: 1536, BGE-M3: 1024, Gemini text-embedding-004: 768)",
    )

    # Capability toggles
    embedding_enabled: bool = Field(True, description="Enable embedding models")
    vision_enabled: bool = Field(False, description="Enable vision/image analysis")
    audio_enabled: bool = Field(True, description="Enable STT/speech-to-text")
    tts_enabled: bool = Field(False, description="Enable TTS/text-to-speech")
    auto_select_model: bool = Field(
        False, description="Auto-select best model per task"
    )

    # ── Фото-кэш ──
    photo_cache_ttl_sec: int = Field(300, description="TTL кэша фотографий (секунды)")

    # ── Key Rotation (KEK/DEK) ──
    key_rotation_enabled: bool = Field(
        False, description="Включить KEK/DEK ротацию ключей шифрования"
    )
    key_rotation_interval_days: int = Field(
        30, description="Интервал ротации DEK (дни)"
    )

    # ── Message classifier ──
    classifier_enabled: bool = Field(
        True, description="Включить Trie/Aho-Corasick классификатор сообщений"
    )

    # ── Smart LLM Routing ──
    smart_routing_enabled: bool = Field(
        True,
        description="Включить умный выбор лёгкой/тяжёлой модели по сложности запроса",
    )

    # ── Smart Extract Optimization ──
    smart_extract_optimized: bool = Field(
        True,
        description="Включить оптимизации smart-извлечения: пропуск тривиальных, кэш, приоритеты, лёгкая модель",
    )
    extract_priority_threshold: float = Field(
        0.3,
        description="Порог приоритетности для извлечения фактов (0.0–1.0). Сообщения с score ниже — пропускаются.",
    )
    extract_cache_ttl: int = Field(
        300,
        description="TTL кэша результатов извлечения фактов (секунды)",
    )

    # ── Streaming ──
    streaming_enabled: bool = Field(True, description="Включить streaming-ответы")
    streaming_edit_interval: float = Field(
        0.3, description="Интервал обновления streaming (сек)"
    )
    streaming_cursor: str = Field(" 🦊", description="Курсор при streaming")

    memory_warmup_idle_timeout_sec: int = Field(
        86400, description="Таймаут простоя для сброса warmup-счётчика (24 часа)"
    )
    memory_warmup_max_contacts: int = Field(
        10,
        description="Макс контактов при штатной экстракции (в warmup — все контакты)",
    )

    # Авто-пересборка профиля каждые N новых личных фактов (0 = только вручную)
    persona_trigger_every_n_facts: int = Field(
        default=15,
        description="Trigger persona rebuild every N new personal facts",
    )

    # --- Telegram API credentials (опционально — нужны только для userbot-режима) ---
    api_id: int | None = Field(
        default=None, description="Telegram API ID from https://my.telegram.org"
    )
    api_hash: str | None = Field(
        default=None,
        description="Telegram API hash from https://my.telegram.org",
    )

    disk_critical_mb: int = Field(
        100, description="Критический порог свободного места (MB)"
    )
    disk_warning_mb: int = Field(
        500, description="Предупредительный порог свободного места (MB)"
    )
    disk_monitor_interval_sec: int = Field(600, description="Интервал проверки диска")

    # Memory
    # TODO: implement or remove — max_recall_cache_size was deleted (duplicate of recall_cache_max_size)
    memory_consolidation_interval_sec: int = Field(
        21600, description="Интервал консолидации памяти (6 часов)"
    )
    memory_queue_maxsize: int = Field(
        200, description="Максимальный размер очереди фоновой обработки памяти"
    )
    memory_queue_put_timeout: float = Field(
        30.0, description="Таймаут enqueue перед сбросом задания (секунды)"
    )

    # ── Recall defaults ──
    recall_default_limit: int = Field(8, description="Default recall limit")
    recall_max_limit: int = Field(20, description="Max recall limit")
    recall_max_prefetch: int = Field(
        500,
        description="Hard ceiling on pre-fetch query rows "
        "(был безлимитный ×40 → до 2000, теперь capped для масштабирования; "
        "по умолчанию 500 = обратная совместимость с floor deep-режима)",
    )
    recall_semantic_threshold: float = Field(
        0.55, description="Min cosine similarity for semantic search"
    )
    recall_rrf_k: int = Field(60, description="RRF k-parameter")
    recall_mmr_lambda: float = Field(
        0.7, description="MMR lambda (relevance vs diversity)"
    )

    # ── Ebbinghaus retention scoring ──
    ebbinghaus_decay_base: float = Field(
        0.07, description="Base decay rate for Ebbinghaus retention (no recall boost)"
    )
    ebbinghaus_access_weight: float = Field(
        0.5, description="Weight of access count in retention boost"
    )
    auto_forget_threshold: float = Field(
        0.15,
        description="Retention score below which facts are candidates for forgetting",
    )
    auto_forget_enabled: bool = Field(
        True, description="Enable automatic forgetting of low-retention facts"
    )
    contradiction_supersedes_window_minutes: int = Field(
        30,
        description="Окно поиска supersedes-связанного факта после противоречия (минуты)",
    )

    # ── Dreaming V3 — LLM semantic re-evaluation of stale facts ──
    dreaming_reval_enabled: bool = Field(
        True,
        description="Enable LLM-driven semantic re-evaluation in dream cycle",
    )
    dreaming_reval_max_per_run: int = Field(
        50,
        description="Max facts to re-evaluate per nightly dream cycle run",
    )
    dreaming_reval_confidence_threshold: float = Field(
        0.5,
        description="Min confidence to consider a fact for re-evaluation",
    )
    dreaming_reval_lookback_days: int = Field(
        365,
        description="Skip facts older than this; auto-forget handles them",
    )
    dreaming_reval_concurrency: int = Field(
        3,
        description=(
            "Max parallel LLM calls during revaluation. Matches the 'background' "
            "purpose Semaphore in router; raise to use more keys concurrently."
        ),
    )

    # ── OpenTelemetry ──
    otel_enabled: bool = Field(False, description="Enable OpenTelemetry tracing")
    otel_exporter_endpoint: str = Field(
        "", description="OTLP exporter endpoint (e.g. http://localhost:4318/v1/traces)"
    )

    # ── Limits & timeouts ──
    max_message_length: int = Field(4096, description="Telegram max message length")
    safe_message_length: int = Field(4000, description="Buffer before Telegram limit")
    max_voice_queue_size: int = Field(20, description="Max voice messages in queue")
    voice_queue_timeout: float = Field(
        10.0, description="Seconds before dropping voice msg"
    )

    # ── Route Cache (S2-T1) ──
    route_cache_enabled: bool = Field(
        True,
        description="Кэшировать маршрутные решения RouterPlan (S2-T1 Pattern Cache)",
    )

    # ── LLM Response Cache ──
    response_cache_enabled: bool = Field(
        True, description="Кэшировать ответы LLM (SmartCache)"
    )
    response_cache_ttl: int = Field(
        300, description="TTL кэша ответов LLM по умолчанию (секунды)"
    )

    # ── Caching ──
    context_cache_max_size: int = Field(2000, description="Max context cache entries")
    contact_digest_cache_max: int = Field(
        500, description="Max contact digest cache entries"
    )
    recall_cache_max_size: int = Field(1000, description="Max recall cache entries")
    recall_cache_result_ttl: float = Field(
        30.0, description="Recall cache TTL with facts (sec)"
    )
    recall_cache_empty_ttl: float = Field(
        60.0, description="Recall cache TTL without facts (sec)"
    )

    # ── Contact prefetch ──
    contact_prefetch_enabled: bool = Field(
        True, description="Prefetch contacto data at message handler start"
    )
    contact_cache_ttl: int = Field(
        300, description="Contact prefetch cache TTL in seconds (5 min)"
    )

    # Humanizer
    humanizer_deep_min_length: int = Field(
        100, description="Минимальная длина текста для deep humanizer"
    )
    humanizer_deep_min_score: float = Field(
        0.3, description="Минимальный AI-score для deep humanizer"
    )

    # Tool loop
    max_tool_iterations: int = Field(
        5, description="Макс. итераций tool-calling в Maestro"
    )

    # ── Skill Evolution (SkillOpt-inspired) ──
    skill_edit_budget: int = Field(
        3,
        description="Макс. количество bounded edits за одну итерацию (textual learning rate)",
    )
    skill_optimizer_model: str = Field(
        "",
        description="Модель для оптимизации навыков (пустая = использовать heavy). "
        "Формат: 'provider/model' или 'model_name'",
    )
    skill_target_model: str = Field(
        "",
        description="Целевая модель для исполнения навыков (пустая = использовать light). "
        "Формат: 'provider/model' или 'model_name'",
    )
    skill_validation_enabled: bool = Field(
        True,
        description="Включить validation gate для обновлений навыков",
    )
    skill_auto_edit_enabled: bool = Field(
        True,
        description="Разрешить автоматические bounded edits вместо полной замены навыков",
    )
    skill_edit_cooldown_sec: int = Field(
        60,
        description="Минимальный интервал между edits одного навыка (rate limiting)",
    )
    skill_auto_evolve_interval_sec: int = Field(
        21600,  # 6 hours
        description="Интервал auto-evolution цикла (по умолчанию 6 часов)",
    )
    skill_auto_evolve_min_failures: int = Field(
        3,
        description="Минимальное количество провалов для запуска auto-evolution навыка",
    )

    # Pre-gate pattern filtering
    pre_gate_extended: bool = Field(
        True, description="Enable extended pre-gate pattern matching (100+ patterns)"
    )

    # Smart correction
    # ── Auto-save facts batching ──
    auto_save_batch_enabled: bool = Field(
        True,
        description="Объединять авто-сохранение фактов в батчи (экономит LLM-вызовы)",
    )
    auto_save_batch_size: int = Field(
        5, description="Размер батча для авто-сохранения фактов"
    )
    auto_save_batch_timeout: float = Field(
        10.0, description="Таймаут сброса батча авто-сохранения (секунды)"
    )
    auto_save_batch_max_wait: float = Field(
        60.0,
        description="Максимальное время жизни батча — flush независимо от активности (секунды)",
    )

    smart_correction_action_ttl: float = Field(
        60.0, description="TTL хранимых действий для smart-correction (сек)"
    )

    # Pending
    pending_ttl_sec: int = Field(
        300, description="TTL ожидающих подтверждений (5 минут)"
    )

    # Auto-reply
    auto_reply_global_limit_per_hour: int = Field(
        100, description="Глобальный лимит авто-ответов в час"
    )

    # Context
    context_max_turns: int = Field(50, description="Макс. витков диалога перед сжатием")

    # ── Skill seeding ──
    skill_seed_on_startup: bool = Field(
        True, description="Auto-seed skills from skills/*/SKILL.md on startup"
    )

    # Agent/task-specific model overrides (из .env)
    maestro_model: str = Field("", description="Model override for maestro agent")
    draft_model: str = Field("", description="Model override for draft agent")
    memory_model: str = Field("", description="Model override for memory agent")
    search_model: str = Field("", description="Model override for search agent")
    humanize_model: str = Field("", description="Model override for humanize agent")
    classify_model: str = Field("", description="Model override for classify agent")
    summarize_model: str = Field("", description="Model override for summarize agent")
    skills_model: str = Field("", description="Model override for skills agent")
    background_model: str = Field("", description="Model override for background tasks")
    vision_model: str = Field("", description="Model override for vision tasks")

    @property
    def data_dir(self) -> Path:
        path = PROJECT_ROOT / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
