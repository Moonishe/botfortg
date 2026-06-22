"""UI rendering for settings — main menu and keyboard helpers.

SRP: pure presentation — no DB write logic, no handlers.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.bot.callbacks import SettingsCB
from src.db.models._auth import ApiKey
from src.db.repo import get_or_create_user, list_key_slots
from src.db.session import get_session
from src.core.infra.timeutil import tz_short


def _check(value: bool) -> str:
    return "✅" if value else "❌"


def _back_row(parent: str = "menu"):
    return [
        InlineKeyboardButton(text="🔙 Назад", callback_data=SettingsCB.back(parent))
    ]


async def _render_menu(telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        s = owner.settings
        # Single batch query for all API keys (was 9 individual queries)
        _api_key_rows = (
            (await session.execute(select(ApiKey).where(ApiKey.user_id == owner.id)))
            .scalars()
            .all()
        )
        _keys_map: dict[str, bool] = {r.provider: True for r in _api_key_rows}
        openai_key = _keys_map.get("openai", False)
        gemini_key = _keys_map.get("gemini", False)
        mistral_key = _keys_map.get("mistral", False)
        cloudflare_key = _keys_map.get("cloudflare", False)
        deepseek_key = _keys_map.get("deepseek", False)
        grok_key = _keys_map.get("grok", False)
        mimo_key = _keys_map.get("mimo", False)
        groq_key = _keys_map.get("groq", False)
        custom_key = _keys_map.get("custom", False)

        # Also check LlmKeySlot for custom/stt providers
        try:
            all_slots = await list_key_slots(session, owner)
            has_custom_slots = any(
                s.provider
                not in {
                    "openai",
                    "gemini",
                    "mistral",
                    "deepseek",
                    "cloudflare",
                    "grok",
                    "mimo",
                    "groq",
                    "deepgram",
                    "assemblyai",
                }
                and s.enabled
                for s in all_slots
            )
            has_deepgram = any(
                s.provider == "deepgram" and s.enabled for s in all_slots
            )
            has_assemblyai = any(
                s.provider == "assemblyai" and s.enabled for s in all_slots
            )
        except (SQLAlchemyError, AttributeError):
            has_custom_slots = False
            has_deepgram = False
            has_assemblyai = False

        custom_ok = bool(custom_key) or has_custom_slots

        # ── Extract ORM values to local vars (session-safe) ──────────
        _tz = s.timezone
        _auto_reply_enabled = s.auto_reply_enabled
        _auto_reply_cooldown_min = s.auto_reply_cooldown_min
        _auto_sync_enabled = getattr(s, "auto_sync_enabled", True)
        _auto_sync_interval_sec = getattr(s, "auto_sync_interval_sec", 7200)
        _auto_extract_memories = getattr(s, "auto_extract_memories", False)
        _include_saved_messages = getattr(s, "include_saved_messages", False)
        _digest_enabled = s.digest_enabled
        _digest_time = s.digest_time
        _reminders_enabled = s.reminders_enabled
        _reminder_lead_hours = s.reminder_lead_hours
        _reminder_overdue_enabled = s.reminder_overdue_enabled
        _news_enabled = s.news_enabled
        _news_window_hours = s.news_window_hours
        _ignore_archived = s.ignore_archived
        _smart_digest_enabled = getattr(s, "smart_digest_enabled", False)
        _smart_digest_interval_min = getattr(s, "smart_digest_interval_min", 30)
        _llm_provider = s.llm_provider
        _use_heavy_model = s.use_heavy_model
        _transcription_mode = s.transcription_mode
        _transcription_api_provider = getattr(s, "transcription_api_provider", "openai")

    text = (
        "⚙ <b>Настройки</b>\n\n"
        f"🌍 Часовой пояс: <b>{tz_short(_tz)}</b>\n"
        f"🔄 Авто: ответ {_check(_auto_reply_enabled)} ({_auto_reply_cooldown_min}м) · синк {_check(_auto_sync_enabled)} ({_auto_sync_interval_sec}с)\n"
        f"🧠 Авто-память: {_check(_auto_extract_memories)}\n"
        f"⭐ Избранное: {_check(_include_saved_messages)}\n"
        f"☀ Дайджест: {_check(_digest_enabled)} ({_digest_time}) · smart: {_check(_smart_digest_enabled)} ({_smart_digest_interval_min}м)\n"
        f"⏰ Напоминания: {_check(_reminders_enabled)} (за {_reminder_lead_hours}ч; просрочки {_check(_reminder_overdue_enabled)})\n"
        f"📰 Новости: {_check(_news_enabled)} (окно {_news_window_hours}ч)\n"
        f"🛡 Игнорировать архив: {_check(_ignore_archived)}\n"
        f"🧠 LLM: <b>{_llm_provider}</b> · {'тяжёлая' if _use_heavy_model else 'лёгкая'} · tr: {_transcription_mode}\n"
        f"🔑 Ключи: OpenAI {_check(bool(openai_key))} · Gemini {_check(bool(gemini_key))} · Mistral {_check(bool(mistral_key))} · DeepSeek {_check(bool(deepseek_key))} · Cloudflare {_check(bool(cloudflare_key))} · Grok {_check(bool(grok_key))} · MiMo {_check(bool(mimo_key))} · Groq {_check(bool(groq_key))} · Deepgram {_check(has_deepgram)} · AssemblyAI {_check(has_assemblyai)} · Свой {_check(custom_ok)}\n\n"
        "<i>Тапни раздел, чтобы открыть его настройки и описание.</i>"
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🌍 Часовой пояс", callback_data=SettingsCB.section("tz")
        ),
        InlineKeyboardButton(
            text="🔄 Авто-ответ", callback_data=SettingsCB.section("auto_reply")
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="🤖 Авто-режим", callback_data=SettingsCB.section("auto_mode")
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="🌅 Дайджест", callback_data=SettingsCB.section("digest")
        ),
        InlineKeyboardButton(
            text="⏰ Напоминания", callback_data=SettingsCB.section("reminders")
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="📊 Smart-дайджест", callback_data=SettingsCB.section("smart_digest")
        ),
        InlineKeyboardButton(
            text="📰 Новости", callback_data=SettingsCB.section("news")
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="🧠 LLM и модели", callback_data=SettingsCB.section("brain")
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="✍️ Черновики", callback_data=SettingsCB.section("drafts")
        ),
        InlineKeyboardButton(
            text="🔒 Приватность", callback_data=SettingsCB.section("privacy")
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="🔄 Синхронизация", callback_data=SettingsCB.section("sync")
        ),
        InlineKeyboardButton(
            text="🔑 API-ключи", callback_data=SettingsCB.section("keys")
        ),
    )
    kb.row(InlineKeyboardButton(text="📬 Треды", callback_data="thread:refresh"))
    kb.row(
        InlineKeyboardButton(text="🧠 Полный анализ", callback_data=SettingsCB.ANALYZE)
    )
    kb.row(
        InlineKeyboardButton(
            text="🎭 Личность", callback_data=SettingsCB.section("personality")
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="📤 Экспорт конфига", callback_data=SettingsCB.EXPORT_CONFIG
        ),
        InlineKeyboardButton(
            text="📥 Импорт конфига", callback_data=SettingsCB.IMPORT_CONFIG
        ),
    )
    kb.row(InlineKeyboardButton(
        text="🧠 Memory & AI",
        callback_data=SettingsCB.section("memory_ai"),
    ))
    kb.row(InlineKeyboardButton(text="❌ Закрыть", callback_data=SettingsCB.CLOSE))
    # Быстрые тогглы (авто-память, избранное, дайджест, авто-ответ)
    text += "\n⚡ <b>Быстрые тогглы:</b>"
    kb.row(
        InlineKeyboardButton(
            text=f"🧠 Авто-память {_check(_auto_extract_memories)}",
            callback_data=SettingsCB.toggle("auto_extract_memories"),
        ),
        InlineKeyboardButton(
            text=f"⭐ Избранное {_check(_include_saved_messages)}",
            callback_data=SettingsCB.toggle("include_saved_messages"),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text=f"☀ Дайджест {_check(_digest_enabled)}",
            callback_data=SettingsCB.toggle("digest_enabled"),
        ),
        InlineKeyboardButton(
            text=f"🔄 Авто-ответ {_check(_auto_reply_enabled)}",
            callback_data=SettingsCB.toggle("auto_reply_enabled"),
        ),
    )
    return text, kb.as_markup()
