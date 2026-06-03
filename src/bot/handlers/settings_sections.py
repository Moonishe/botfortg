"""Per-section rendering and FSM (input) handlers for settings.

SRP: section UI building + all FSM input flows — no router creation, no main handlers.
"""

import json
import logging

from aiogram import F
from aiogram.exceptions import TelegramBadRequest, TelegramError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.callbacks import SettingsCB
from src.bot.handlers.settings import router
from src.bot.handlers.settings_menu import _back_row, _check, _render_menu
from src.bot.handlers.settings_service import _count_slots_for_provider
from src.bot.states import SettingsStates
from src.config import settings as app_config
from src.core.infra.key_guard import safe_str
from src.core.infra.text_sanitizer import sanitize_html
from src.core.infra.timeutil import HM_RE, TZ_PRESETS, is_valid_tz, tz_short
from src.db.repo import (
    add_key_slot,
    get_api_key,
    get_or_create_user,
    get_persona,
    list_folders,
    list_key_slots,
    upsert_api_key,
)
from src.db.session import get_session
from src.userbot import get_active_telethon_client, get_userbot_manager
from src.userbot.dialogs import sync_dialogs

from src.bot.handlers._key_input import make_key_handler

# ── LLM provider imports for key validation ──
from src.llm.cloudflare_provider import CloudflareProvider
from src.llm.deepseek_provider import DeepSeekProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.grok_provider import GrokProvider
from src.llm.groq_provider import GroqProvider
from src.llm.mimo_provider import MIMO_REGIONS, MiMoProvider
from src.llm.mistral_provider import MistralProvider
from src.llm.openai_provider import OpenAIProvider
from src.llm.custom_provider import CustomProvider
from openai import APIConnectionError
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


# =====================================================================
#  SECTION RENDERING
# =====================================================================


async def _render_section(
    telegram_id: int, section: str
) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        s = owner.settings
        openai_key = await get_api_key(session, owner, "openai")
        gemini_key = await get_api_key(session, owner, "gemini")
        mistral_key = await get_api_key(session, owner, "mistral")
        cloudflare_key = await get_api_key(session, owner, "cloudflare")
        deepseek_key = await get_api_key(session, owner, "deepseek")
        grok_key = await get_api_key(session, owner, "grok")
        mimo_key = await get_api_key(session, owner, "mimo")
        groq_key = await get_api_key(session, owner, "groq")
        custom_key = await get_api_key(session, owner, "custom")

        kb = InlineKeyboardBuilder()

        if section == "auto_reply":
            mode_label = (
                "🤖 умный (LLM в твоём стиле)"
                if s.auto_reply_mode == "smart"
                else "📝 заготовленный текст"
            )
            snippet = (s.auto_reply_text or "").strip().replace("\n", " ")
            if len(snippet) > 80:
                snippet = snippet[:77] + "…"
            text = (
                "🔄 <b>Авто-ответ</b>\n\n"
                "Когда я <b>оффлайн</b> и приходит личное сообщение — бот отправляет ответ.\n"
                "Только ЛС, не группы и не боты. Один ответ на контакт раз в кулдаун.\n\n"
                "<b>Режимы</b>:\n"
                "• <b>заготовленный</b> — отправляется один и тот же текст (ниже).\n"
                "• <b>умный</b> — LLM пишет короткий ответ в твоём стиле, опираясь на контекст переписки.\n\n"
                f"Статус: <b>{'ВКЛ' if s.auto_reply_enabled else 'ВЫКЛ'}</b>\n"
                f"Режим: <b>{mode_label}</b>\n"
                f"Кулдаун: <b>{s.auto_reply_cooldown_min} мин</b>\n"
                f"Текст заготовки:\n<i>«{snippet}»</i>"
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.auto_reply_enabled)} Включить авто-ответ",
                    callback_data=SettingsCB.toggle("auto_reply_enabled"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text=("• " if s.auto_reply_mode == "static" else "")
                    + "📝 Заготовка",
                    callback_data=SettingsCB.choose("auto_reply_mode", "static"),
                ),
                InlineKeyboardButton(
                    text=("• " if s.auto_reply_mode == "smart" else "") + "🤖 Умный",
                    callback_data=SettingsCB.choose("auto_reply_mode", "smart"),
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text="✏ Изменить текст заготовки",
                    callback_data=SettingsCB.input("auto_reply_text"),
                )
            )
            kb.row(
                *[
                    InlineKeyboardButton(
                        text=("• " if s.auto_reply_cooldown_min == m else "") + f"{m}м",
                        callback_data=SettingsCB.choose(
                            "auto_reply_cooldown_min", str(m)
                        ),
                    )
                    for m in (5, 15, 30, 60)
                ]
            )
            kb.row(*_back_row())

        elif section == "digest":
            text = (
                "☀ <b>Утренний дайджест</b>\n\n"
                "Раз в сутки в указанное время получаю сводку: что произошло за ночь, кто ждёт ответа, "
                "горящие обещания и сколько было авто-ответов.\n\n"
                f"Статус: <b>{'ВКЛ' if s.digest_enabled else 'ВЫКЛ'}</b>\n"
                f"Время: <b>{s.digest_time}</b> · {tz_short(s.timezone)}\n\n"
                "Часовой пояс — отдельный раздел в /settings.\n"
                "Для разовой сводки — команда /digest"
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.digest_enabled)} Включить дайджест",
                    callback_data=SettingsCB.toggle("digest_enabled"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"⏰ Время: {s.digest_time}",
                    callback_data=SettingsCB.input("digest_time"),
                )
            )
            kb.row(*_back_row())

        elif section == "reminders":
            text = (
                "⏰ <b>Напоминания о дедлайнах</b>\n\n"
                "Бот подгружает обещания из переписок (см. /todos и кнопку «Задачи» в /chat) и пинает, "
                "когда дедлайн близок или просрочен.\n\n"
                f"Статус: <b>{'ВКЛ' if s.reminders_enabled else 'ВЫКЛ'}</b>\n"
                f"Заранее за: <b>{s.reminder_lead_hours} ч</b>\n"
                f"Алерт о просрочках: <b>{'ВКЛ' if s.reminder_overdue_enabled else 'ВЫКЛ'}</b>"
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.reminders_enabled)} Включить напоминания",
                    callback_data=SettingsCB.toggle("reminders_enabled"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.reminder_overdue_enabled)} Алерт при просрочке",
                    callback_data=SettingsCB.toggle("reminder_overdue_enabled"),
                )
            )
            kb.row(
                *[
                    InlineKeyboardButton(
                        text=("• " if s.reminder_lead_hours == h else "") + f"{h}ч",
                        callback_data=SettingsCB.choose("reminder_lead_hours", str(h)),
                    )
                    for h in (1, 2, 4, 12, 24)
                ]
            )
            kb.row(*_back_row())

        elif section == "smart_digest":
            text = (
                "📊 <b>Smart дайджест</b>\n\n"
                "Входящие сообщения за последние N минут собираются в один дайджест "
                "с группировкой по срочности (🔴 срочное → 🟡 важное → 🟢 обычное).\n\n"
                f"Smart дайджест: <b>{'ВКЛ' if s.smart_digest_enabled else 'ВЫКЛ'}</b>\n"
                f"Интервал: <b>{s.smart_digest_interval_min} мин</b>\n"
                f"Мгновенные 🔴 уведомления: <b>{'ВКЛ' if s.urgent_notify_enabled else 'ВЫКЛ'}</b>\n\n"
                "Мгновенные уведомления приходят сразу при получении срочного сообщения.\n"
                "Дайджест собирает все сообщения за интервал и присылает единый отчёт.\n"
                "Ручной запуск: /smart_digest"
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.smart_digest_enabled)} Включить smart дайджест",
                    callback_data=SettingsCB.toggle("smart_digest_enabled"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.urgent_notify_enabled)} Мгновенные 🔴 уведомления",
                    callback_data=SettingsCB.toggle("urgent_notify_enabled"),
                )
            )
            kb.row(
                *[
                    InlineKeyboardButton(
                        text=("• " if s.smart_digest_interval_min == m else "")
                        + f"{m}мин",
                        callback_data=SettingsCB.choose(
                            "smart_digest_interval_min", str(m)
                        ),
                    )
                    for m in (15, 30, 60, 120)
                ]
            )
            kb.row(*_back_row())

        elif section == "news":
            text = (
                "📰 <b>Новости</b>\n\n"
                "Команда <code>/news тема</code> ищет посты в твоих подписанных каналах за последние N часов и "
                "собирает структурированный обзор.\n\n"
                "<b>Авто-новости</b> (этот тогглер): если включено, каждое утро в указанное время бот шлёт "
                "дайджест по каждой теме из <b>/news_topics</b>.\n\n"
                "Чтобы ограничить выборку конкретными каналами — /news_channels.\n\n"
                f"Авто-новости: <b>{'ВКЛ' if s.news_enabled else 'ВЫКЛ'}</b>\n"
                f"Время отправки: <b>{s.news_digest_time}</b> · {tz_short(s.timezone)}\n"
                f"Окно по умолчанию: <b>{s.news_window_hours} ч</b>"
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.news_enabled)} Включить авто-новости",
                    callback_data=SettingsCB.toggle("news_enabled"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"⏰ Время: {s.news_digest_time}",
                    callback_data=SettingsCB.input("news_time"),
                )
            )
            kb.row(
                *[
                    InlineKeyboardButton(
                        text=("• " if s.news_window_hours == h else "") + f"{h}ч",
                        callback_data=SettingsCB.choose("news_window_hours", str(h)),
                    )
                    for h in (6, 12, 24, 48, 72)
                ]
            )
            kb.row(
                InlineKeyboardButton(
                    text="📋 Темы → /news_topics",
                    callback_data=SettingsCB.noop("news_topics"),
                )
            )
            kb.row(*_back_row())

        elif section == "brain":
            _provider_model_names = {
                "openai": ("gpt-5-mini", "gpt-5.5"),
                "gemini": ("gemini-3-flash", "gemini-3.1-pro"),
                "mistral": ("mistral-small-latest", "mistral-medium-latest"),
                "cloudflare": (
                    "@cf/qwen/qwen3-30b-a3b-fp8",
                    "@cf/moonshotai/kimi-k2.6",
                ),
                "deepseek": ("deepseek-chat", "deepseek-reasoner"),
                "grok": ("grok-4.3", "grok-4.20-0309-reasoning"),
                "mimo": ("mimo-v2-flash", "mimo-v2.5-pro"),
                "groq": ("llama-3.3-70b-versatile", "mixtral-8x7b-32768"),
            }
            _names = _provider_model_names.get(s.llm_provider)
            if _names is None:
                try:
                    slots = await list_key_slots(
                        session, owner, provider=s.llm_provider
                    )
                    models = [slot.model for slot in slots if slot.model]
                    _names = (", ".join(models[:3]), "") if models else ("?", "?")
                except (SQLAlchemyError, AttributeError):
                    _names = ("?", "?")
            active = (
                "DeepSeek V4 Flash (бесплатно)"
                if s.llm_provider == "openrouter"
                else _names[1]
                if s.use_heavy_model
                else _names[0]
            )

            api_provider = getattr(s, "transcription_api_provider", "openai")
            tts_labels = {
                "openai": "OpenAI Whisper",
                "gemini": "Gemini (бесплатно)",
                "mistral": "Mistral (бесплатно)",
                "deepgram": "Deepgram",
                "assemblyai": "AssemblyAI",
            }
            api_label = tts_labels.get(api_provider, "OpenAI Whisper")

            text = (
                "🧠 <b>LLM и модели</b>\n\n"
                "━━━ 🤖 Провайдер ━━━\n"
                f"Провайдер: <b>{s.llm_provider}</b>\n"
                f"Режим: <b>{'тяжёлая' if s.use_heavy_model else 'лёгкая'}</b>\n"
                f"Модель: <code>{active}</code>\n\n"
                "━━━ 🎤 Транскрипция ━━━\n"
                f"Режим: <b>{s.transcription_mode}</b> · {api_label}\n\n"
                "━━━ 🧠 Модели задач ━━━\n"
                "<i>Настрой модель под каждую задачу</i>"
            )

            try:
                overrides = json.loads(s.model_overrides) if s.model_overrides else {}
            except (json.JSONDecodeError, TypeError):
                overrides = {}
            if overrides:
                ov_count = len(overrides)
                text += (
                    f"\n⚠️ <b>Активны переопределения ({ov_count} задач)</b> — "
                    "нажми «🧠 Модели задач» чтобы посмотреть"
                )

            kb.row(
                InlineKeyboardButton(
                    text=("• " if s.llm_provider == "openai" else "") + "OpenAI",
                    callback_data=SettingsCB.choose("llm_provider", "openai"),
                ),
                InlineKeyboardButton(
                    text=("• " if s.llm_provider == "gemini" else "") + "Gemini",
                    callback_data=SettingsCB.choose("llm_provider", "gemini"),
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text=("• " if s.llm_provider == "openrouter" else "")
                    + "🔥 DeepSeek (free)",
                    callback_data=SettingsCB.choose("llm_provider", "openrouter"),
                ),
                InlineKeyboardButton(
                    text=("• " if s.llm_provider == "mistral" else "")
                    + "Mistral (free)",
                    callback_data=SettingsCB.choose("llm_provider", "mistral"),
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text=("• " if s.llm_provider == "cloudflare" else "")
                    + "Cloudflare",
                    callback_data=SettingsCB.choose("llm_provider", "cloudflare"),
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text=("• " if s.llm_provider == "deepseek" else "") + "DeepSeek",
                    callback_data=SettingsCB.choose("llm_provider", "deepseek"),
                ),
                InlineKeyboardButton(
                    text=("• " if s.llm_provider == "grok" else "") + "Grok (xAI)",
                    callback_data=SettingsCB.choose("llm_provider", "grok"),
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text=("• " if s.llm_provider == "mimo" else "") + "MiMo (Xiaomi)",
                    callback_data=SettingsCB.choose("llm_provider", "mimo"),
                ),
                InlineKeyboardButton(
                    text=("• " if s.llm_provider == "groq" else "") + "Groq",
                    callback_data=SettingsCB.choose("llm_provider", "groq"),
                ),
            )
            try:
                custom_slots = await list_key_slots(session, owner)
                custom_names = sorted(
                    {
                        s.provider
                        for s in custom_slots
                        if s.provider
                        not in {
                            "openai",
                            "gemini",
                            "mistral",
                            "deepseek",
                            "cloudflare",
                            "grok",
                            "mimo",
                            "groq",
                            "openrouter",
                        }
                        and s.enabled
                    }
                )
            except (SQLAlchemyError, AttributeError):
                custom_names = []
            if custom_names:
                for cn in custom_names:
                    kb.row(
                        InlineKeyboardButton(
                            text=("• " if s.llm_provider == cn else "") + cn,
                            callback_data=SettingsCB.choose("llm_provider", cn),
                        )
                    )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.use_heavy_model)} Тяжёлая модель",
                    callback_data=SettingsCB.toggle("use_heavy_model"),
                )
            )

            for mode in ("local", "api", "hybrid"):
                kb.row(
                    InlineKeyboardButton(
                        text=("• " if s.transcription_mode == mode else "") + mode,
                        callback_data=SettingsCB.choose("transcription_mode", mode),
                    )
                )
            for prov in ("openai", "gemini", "mistral", "deepgram", "assemblyai"):
                prov_label = tts_labels.get(prov, prov)
                kb.row(
                    InlineKeyboardButton(
                        text=("• " if api_provider == prov else "") + prov_label,
                        callback_data=SettingsCB.choose(
                            "transcription_api_provider", prov
                        ),
                    )
                )

            emb_on = app_config.embedding_enabled
            vis_on = app_config.vision_enabled
            aud_on = app_config.audio_enabled
            tts_on = app_config.tts_enabled
            auto_on = app_config.auto_select_model

            text += (
                "\n\n⚙️ <b>Возможности AI (глобальные):</b>\n"
                f"🔤 Embedding: {'✅' if emb_on else '❌'}  👁️ Vision: {'✅' if vis_on else '❌'}\n"
                f"🎤 STT/Audio: {'✅' if aud_on else '❌'}  🔊 TTS: {'✅' if tts_on else '❌'}\n"
                f"🤖 Авто-выбор: {'✅' if auto_on else '❌'}\n"
                f"<i>Настрой через .env / переменные окружения</i>"
            )

            try:
                from src.core.context.engine import ContextEngine

                stats = (
                    ContextEngine.get_load_stats()
                    if hasattr(ContextEngine, "get_load_stats")
                    else None
                )
            except (ImportError, AttributeError, TypeError):
                stats = None
            if stats:
                pct = min(100, int(stats.get("used_pct", 0)))
                bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                text += (
                    f"\n\n📊 <b>Контекст Maestro:</b> [{bar}] {pct}%\n"
                    f"   Память: {stats.get('memory_tokens', 0)} · Вектор: {stats.get('vector_tokens', 0)} · Wiki: {stats.get('wiki_tokens', 0)}"
                )
            else:
                text += "\n\n📊 <b>Контекст Maestro:</b> будет доступен после запуска"

            kb.row(
                InlineKeyboardButton(
                    text="🧠 Настроить модели задач →",
                    callback_data=SettingsCB.section("models_brain"),
                )
            )
            kb.row(*_back_row())

        elif section == "models_brain":
            try:
                overrides = json.loads(s.model_overrides) if s.model_overrides else {}
            except (json.JSONDecodeError, TypeError):
                overrides = {}

            task_labels = {
                "maestro": "🎭 Maestro (оркестрация)",
                "draft": "✍️ Черновики",
                "memory": "🧠 Память",
                "search": "🔍 Поиск",
                "stt": "🎤 Распознавание речи",
                "humanize": "✨ Хуманайзер",
                "classify": "🏷 Классификация",
                "summarize": "📝 Саммари",
                "skills": "🛠 Навыки",
                "background": "🌙 Фоновые задачи",
                "default": "💬 Обычный чат",
            }

            lines = ["🧠 <b>Модели для задач</b>", ""]
            for task_type, label in task_labels.items():
                override = overrides.get(task_type)
                model_str = (
                    f"<code>{override}</code>" if override else "<i>по умолчанию</i>"
                )
                lines.append(f"{label}: {model_str}")
            lines.append("")
            lines.append(
                "<i>Нажми на задачу, чтобы выбрать модель. "
                "Переопределения имеют приоритет над LLM-провайдером.</i>"
            )
            text = "\n".join(lines)

            for task_type, label in task_labels.items():
                kb.row(
                    InlineKeyboardButton(
                        text=label, callback_data=SettingsCB.model_sel(task_type)
                    )
                )
            kb.row(
                InlineKeyboardButton(
                    text="🗑 Сбросить все", callback_data=SettingsCB.MODEL_RESET_ALL
                )
            )
            kb.row(*_back_row("brain"))

        elif section.startswith("model_sel:"):
            task_type = section.split(":", 1)[1]

            task_labels = {
                "maestro": "🎭 Maestro (оркестрация)",
                "draft": "✍️ Черновики",
                "memory": "🧠 Память",
                "search": "🔍 Поиск",
                "stt": "🎤 Распознавание речи",
                "humanize": "✨ Хуманайзер",
                "classify": "🏷 Классификация",
                "summarize": "📝 Саммари",
                "skills": "🛠 Навыки",
                "background": "🌙 Фоновые задачи",
                "default": "💬 Обычный чат",
            }
            task_label = task_labels.get(task_type, task_type)

            try:
                overrides = json.loads(s.model_overrides) if s.model_overrides else {}
            except (json.JSONDecodeError, TypeError):
                overrides = {}

            current = overrides.get(task_type)

            slots = await list_key_slots(session, owner)
            provider_models: dict[str, set[str]] = {}
            for slot in slots:
                if not slot.enabled:
                    continue
                if slot.provider not in provider_models:
                    provider_models[slot.provider] = set()
                if slot.model:
                    provider_models[slot.provider].add(slot.model)

            from src.llm.provider_catalog import get_provider

            available_models: list[str] = []
            seen: set[str] = set()
            for provider, models in provider_models.items():
                if models:
                    for model in sorted(models):
                        key = f"{provider}/{model}"
                        if key not in seen:
                            seen.add(key)
                            available_models.append(key)
                else:
                    pi = get_provider(provider)
                    if pi:
                        for model in pi.models:
                            key = f"{provider}/{model}"
                            if key not in seen:
                                seen.add(key)
                                available_models.append(key)

            available_models.sort()

            if not available_models:
                pi = get_provider(s.llm_provider)
                if pi and pi.models:
                    available_models = [f"{s.llm_provider}/{m}" for m in pi.models]

            lines = [
                f"🧠 <b>Модель для: {task_label}</b>",
                "",
                f"Текущая: <code>{current}</code>"
                if current
                else "Текущая: <i>по умолчанию</i>",
                "",
            ]
            text = "\n".join(lines)

            kb.row(
                InlineKeyboardButton(
                    text=("• " if not current else "") + "🔄 По умолчанию",
                    callback_data=SettingsCB.model_set(task_type, "__default__"),
                )
            )
            for model in available_models:
                is_selected = current and (
                    current == model or model.endswith(f"/{current}")
                )
                mark = "• " if is_selected else ""
                kb.row(
                    InlineKeyboardButton(
                        text=f"{mark}{model}",
                        callback_data=SettingsCB.model_set(task_type, model),
                    )
                )
            kb.row(
                InlineKeyboardButton(
                    text="✏ Ввести вручную…",
                    callback_data=SettingsCB.model_custom(task_type),
                )
            )
            if current:
                kb.row(
                    InlineKeyboardButton(
                        text="🗑 Удалить переопределение",
                        callback_data=SettingsCB.model_del(task_type),
                    )
                )
            kb.row(*_back_row("models_brain"))

        elif section == "tz":
            text = (
                "🌍 <b>Часовой пояс</b>\n\n"
                "От него отталкиваются:\n"
                "• время утреннего дайджеста и авто-новостей\n"
                "• отображение дедлайнов в /todos и напоминаниях\n"
                "• временные метки в дайджестах\n\n"
                f"Сейчас: <b>{tz_short(s.timezone)}</b>\n\n"
                "Тапни пресет ниже или введи свой IANA-таймзону кнопкой «Другой…»."
            )
            for i in range(0, len(TZ_PRESETS), 2):
                buttons = []
                for tz in TZ_PRESETS[i : i + 2]:
                    mark = "• " if s.timezone == tz else ""
                    buttons.append(
                        InlineKeyboardButton(
                            text=mark + tz, callback_data=SettingsCB.timezone(tz)
                        )
                    )
                kb.row(*buttons)
            kb.row(
                InlineKeyboardButton(
                    text="✏ Другой…", callback_data=SettingsCB.input("timezone")
                )
            )
            kb.row(*_back_row())

        elif section == "privacy":
            folders_data = await list_folders(session, owner)

            try:
                monitored = (
                    json.loads(s.monitored_folders) if s.monitored_folders else []
                )
            except json.JSONDecodeError:
                monitored = []

            text = (
                "🛡 <b>Приватность и видимость</b>\n\n"
                "Что бот <b>смотрит и обрабатывает</b> по умолчанию.\n\n"
                "<b>Игнорировать архив</b> — чаты в архиве Telegram не подгружаются ни в /chat, "
                "ни в /search, ни в /news, ни в авто-ответ. Включено по умолчанию.\n\n"
                f"Игнорировать архив: <b>{'ВКЛ' if s.ignore_archived else 'ВЫКЛ'}</b>\n\n"
                "<i>Изменения вступают в силу для следующих запросов. Архивный статус подтягивается "
                "при /sync.</i>\n\n"
                "━━━ 📁 <b>Мониторинг папок</b> ━━━"
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.ignore_archived)} Игнорировать архив",
                    callback_data=SettingsCB.toggle("ignore_archived"),
                )
            )

            if not folders_data:
                text += "\n\n⚠️ Папки не найдены. Сделай /sync."
            else:
                for f in folders_data:
                    icon = "✅" if f.title in monitored else "⬜"
                    kb.button(
                        text=f"{icon} {f.emoji or '📂'} {f.title}",
                        callback_data=SettingsCB.folder_toggle(f.title),
                    )
                text += (
                    "\n\n<i>Нажимай на папку чтобы включить/выключить мониторинг.</i>"
                )

            monitor_only = "✅" if s.monitor_only_selected_folders else "⬜"
            kb.row(
                InlineKeyboardButton(
                    text=f"{monitor_only} Только выбранные",
                    callback_data=SettingsCB.toggle("monitor_only_selected_folders"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text="🔄 Обновить папки", callback_data=SettingsCB.FOLDER_REFRESH
                )
            )
            kb.row(*_back_row())

        elif section == "sync":
            sync_enabled = getattr(s, "auto_sync_enabled", True)
            sync_sec = getattr(s, "auto_sync_interval_sec", 7200)
            auto_mem = getattr(s, "auto_extract_memories", False)
            saved_msgs = getattr(s, "include_saved_messages", False)
            if sync_sec >= 3600:
                intv = f"{sync_sec // 3600}ч"
            elif sync_sec >= 60:
                intv = f"{sync_sec // 60}м"
            else:
                intv = f"{sync_sec}с"
            text = (
                "🔄 <b>Синхронизация и разведка</b>\n\n"
                "Раз в указанный интервал бот обновляет список контактов и архивный статус.\n\n"
                f"Авто-синк: <b>{'ВКЛ' if sync_enabled else 'ВЫКЛ'}</b> · {intv}\n"
                f"Авто-память: <b>{'ВКЛ' if auto_mem else 'ВЫКЛ'}</b> (после синка извлекает факты без вопроса)\n"
                f"Избранное: <b>{'ВКЛ' if saved_msgs else 'ВЫКЛ'}</b> (индексировать и искать в Избранном)"
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(sync_enabled)} Включить авто-синк",
                    callback_data=SettingsCB.toggle("auto_sync_enabled"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(auto_mem)} Авто-извлечение памяти",
                    callback_data=SettingsCB.toggle("auto_extract_memories"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(saved_msgs)} Индексировать Избранное",
                    callback_data=SettingsCB.toggle("include_saved_messages"),
                )
            )
            for v, label in [
                (60, "1м"),
                (300, "5м"),
                (1800, "30м"),
                (3600, "1ч"),
                (7200, "2ч"),
                (14400, "4ч"),
                (86400, "24ч"),
            ]:
                kb.row(
                    InlineKeyboardButton(
                        text=("• " if sync_sec == v else "") + label,
                        callback_data=SettingsCB.choose(
                            "auto_sync_interval_sec", str(v)
                        ),
                    )
                )
            kb.row(
                InlineKeyboardButton(
                    text="✏ Свой интервал…",
                    callback_data=SettingsCB.input("auto_sync_interval"),
                )
            )
            kb.row(*_back_row())

        elif section == "drafts":
            text = (
                "✍️ <b>Авто-черновики</b>\n\n"
                "Когда приходит новое сообщение — бот может автоматически предложить черновик ответа "
                "с кнопками «Отправить / Редактировать / Игнорировать».\n\n"
                "• <b>Только важные</b> — черновик предлагается только для срочных/важных сообщений "
                "(классификация по тексту).\n"
                "• <b>Лимит</b> — макс. черновиков в час, чтобы не спамить.\n\n"
                f"Статус: <b>{'ВКЛ' if s.draft_suggestions_enabled else 'ВЫКЛ'}</b>\n"
                f"Только важные: <b>{'ВКЛ' if s.draft_only_important else 'ВЫКЛ'}</b>\n"
                f"Лимит: <b>{s.draft_max_per_hour} в час</b>"
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.draft_suggestions_enabled)} Включить авто-черновики",
                    callback_data=SettingsCB.toggle("draft_suggestions_enabled"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.draft_only_important)} Только важные",
                    callback_data=SettingsCB.toggle("draft_only_important"),
                )
            )
            kb.row(
                *[
                    InlineKeyboardButton(
                        text=("• " if s.draft_max_per_hour == m else "") + f"{m}/ч",
                        callback_data=SettingsCB.choose("draft_max_per_hour", str(m)),
                    )
                    for m in (3, 5, 10)
                ]
            )
            kb.row(*_back_row())

        elif section == "keys":
            custom_slots = await list_key_slots(session, owner)
            custom_providers = {
                s.provider
                for s in custom_slots
                if s.provider
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
            }

            has_deepgram = any(
                s.provider == "deepgram" and s.enabled for s in custom_slots
            )
            has_assemblyai = any(
                s.provider == "assemblyai" and s.enabled for s in custom_slots
            )

            text = (
                "🔑 <b>API-ключи</b>\n\n"
                "Хранятся зашифрованными (Fernet). Можно перезаписать в любой момент.\n\n"
                f"OpenAI: {_check(bool(openai_key))}\n"
                f"Gemini: {_check(bool(gemini_key))}\n"
                f"Mistral: {_check(bool(mistral_key))}\n"
                f"DeepSeek: {_check(bool(deepseek_key))}\n"
                f"Cloudflare: {_check(bool(cloudflare_key))}\n"
                f"Grok: {_check(bool(grok_key))}\n"
                f"MiMo: {_check(bool(mimo_key))}\n"
                f"Groq: {_check(bool(groq_key))}\n"
                f"Deepgram: {_check(has_deepgram)}\n"
                f"AssemblyAI: {_check(has_assemblyai)}\n"
                f"Свой: {_check(bool(custom_key))}"
            )
            if custom_providers:
                text += "\n\n🛠 <b>Кастомные провайдеры:</b>"
                for cp in sorted(custom_providers):
                    text += f"\n{cp}: ✅"
            kb.row(
                InlineKeyboardButton(
                    text="🔑 OpenAI key", callback_data=SettingsCB.input("openai_key")
                ),
                InlineKeyboardButton(
                    text="🔑 Gemini key", callback_data=SettingsCB.input("gemini_key")
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text="🔑 Mistral key", callback_data=SettingsCB.input("mistral_key")
                ),
                InlineKeyboardButton(
                    text="🔑 DeepSeek key",
                    callback_data=SettingsCB.input("deepseek_key"),
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text="🔑 Cloudflare key",
                    callback_data=SettingsCB.input("cloudflare_key"),
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text="🔑 Grok key", callback_data=SettingsCB.input("grok_key")
                ),
                InlineKeyboardButton(
                    text="🔑 MiMo key", callback_data=SettingsCB.input("mimo_key")
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text="🔑 Groq key", callback_data=SettingsCB.input("groq_key")
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text="🔑 Deepgram key",
                    callback_data=SettingsCB.input("deepgram_key"),
                ),
                InlineKeyboardButton(
                    text="🔑 AssemblyAI key",
                    callback_data=SettingsCB.input("assemblyai_key"),
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text="➕ Свой провайдер",
                    callback_data=SettingsCB.input("custom_name"),
                ),
            )
            kb.row(*_back_row())

        elif section == "auto_mode":
            mode_labels = {
                "offline_only": "🌙 Только когда оффлайн",
                "always": "🔄 Всегда отвечать",
                "smart": "🧠 Умный режим (по срочности)",
            }
            qh_start = s.quiet_hours_start or "не задано"
            qh_end = s.quiet_hours_end or "не задано"
            close_contacts = _check(s.auto_reply_close_contacts)
            notify = _check(s.notify_on_auto_reply)

            text = (
                "🤖 <b>Авто-режим</b>\n\n"
                "Определяет, когда и как бот отвечает на сообщения.\n\n"
                f"Режим: <b>{mode_labels.get(s.auto_mode, s.auto_mode)}</b>\n"
                f"🔕 Тихие часы: <b>{qh_start} – {qh_end}</b>\n"
                f"{close_contacts} Авто-ответ близким контактам\n"
                f"{notify} Уведомлять об авто-ответах"
            )

            for mode in ("offline_only", "always", "smart"):
                prefix = "• " if s.auto_mode == mode else ""
                kb.button(
                    text=f"{prefix}{mode_labels[mode]}",
                    callback_data=SettingsCB.choose("auto_mode", mode),
                )
            kb.adjust(1)

            kb.row(
                InlineKeyboardButton(
                    text="🔕 Начало тихих часов",
                    callback_data=SettingsCB.input("quiet_hours_start"),
                ),
                InlineKeyboardButton(
                    text="🔕 Конец тихих часов",
                    callback_data=SettingsCB.input("quiet_hours_end"),
                ),
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{_check(s.auto_reply_close_contacts)} Авто-ответ близким",
                    callback_data=SettingsCB.toggle("auto_reply_close_contacts"),
                ),
                InlineKeyboardButton(
                    text=f"{_check(s.notify_on_auto_reply)} Уведомлять об авто-ответах",
                    callback_data=SettingsCB.toggle("notify_on_auto_reply"),
                ),
            )
            kb.row(*_back_row())

        elif section == "personality":
            p = await get_persona(session, owner)

            tone_labels = {
                "default": "По умолчанию",
                "professional": "Профессиональный",
                "friendly": "Дружелюбный",
                "frank": "Откровенный",
                "whimsical": "Причудливый",
                "efficient": "Эффективный",
                "cynical": "Циничный",
            }
            level_labels = {"low": "Менее", "normal": "По умолчанию", "high": "Более"}
            anti_ai_mode_labels = {"off": "Выкл", "log": "Лог", "fix": "Исправлять"}
            current_tone = tone_labels.get(p.base_tone, "По умолчанию")

            text = (
                "🎭 <b>Личность</b>\n\n"
                "<b>Базовый стиль и тон</b>\n"
                f"Сейчас: <b>{current_tone}</b>\n\n"
                "<b>Характеристики</b>\n"
                f"🔥 Теплый: <b>{level_labels.get(p.warmth, '—')}</b>\n"
                f"⚡ Восторженный: <b>{level_labels.get(p.enthusiasm, '—')}</b>\n"
                f"📋 Заголовки и списки: <b>{level_labels.get(p.headings_lists, '—')}</b>\n"
                f"😊 Эмодзи: <b>{level_labels.get(p.emoji_level, '—')}</b>\n\n"
                f"📝 Инструкции: {'есть' if p.custom_instructions else 'нет'}\n"
                f"👤 Псевдоним: {p.alias or 'не задан'}\n"
                f"🧠 Адаптивный режим: <b>{'ВКЛ' if p.adaptive_mode_enabled else 'ВЫКЛ'}</b>\n"
                f"🛡️ Anti-AI: <b>{'ВКЛ' if s.anti_ai_enabled else 'ВЫКЛ'}</b>"
                f" ({anti_ai_mode_labels.get(s.anti_ai_mode, '—')})"
            )

            for tone_key, tone_label in tone_labels.items():
                prefix = "• " if p.base_tone == tone_key else ""
                kb.button(
                    text=f"{prefix}{tone_label}",
                    callback_data=SettingsCB.choose("base_tone", tone_key),
                )
            kb.adjust(2)

            kb.row(
                InlineKeyboardButton(
                    text="🔥 Теплый",
                    callback_data=SettingsCB.noop("warmth"),
                )
            )
            kb.row(
                *[
                    InlineKeyboardButton(
                        text=("• " if p.warmth == lvl else "") + label,
                        callback_data=SettingsCB.choose("warmth", lvl),
                    )
                    for lvl, label in [
                        ("low", "Менее"),
                        ("normal", "По умолч."),
                        ("high", "Более"),
                    ]
                ]
            )

            kb.row(
                InlineKeyboardButton(
                    text="⚡ Восторженный",
                    callback_data=SettingsCB.noop("enthusiasm"),
                )
            )
            kb.row(
                *[
                    InlineKeyboardButton(
                        text=("• " if p.enthusiasm == lvl else "") + label,
                        callback_data=SettingsCB.choose("enthusiasm", lvl),
                    )
                    for lvl, label in [
                        ("low", "Менее"),
                        ("normal", "По умолч."),
                        ("high", "Более"),
                    ]
                ]
            )

            kb.row(
                InlineKeyboardButton(
                    text="📋 Заголовки и списки",
                    callback_data=SettingsCB.noop("headings_lists"),
                )
            )
            kb.row(
                *[
                    InlineKeyboardButton(
                        text=("• " if p.headings_lists == lvl else "") + label,
                        callback_data=SettingsCB.choose("headings_lists", lvl),
                    )
                    for lvl, label in [
                        ("low", "Менее"),
                        ("normal", "По умолч."),
                        ("high", "Более"),
                    ]
                ]
            )

            kb.row(
                InlineKeyboardButton(
                    text="😊 Эмодзи",
                    callback_data=SettingsCB.noop("emoji_level"),
                )
            )
            kb.row(
                *[
                    InlineKeyboardButton(
                        text=("• " if p.emoji_level == lvl else "") + label,
                        callback_data=SettingsCB.choose("emoji_level", lvl),
                    )
                    for lvl, label in [
                        ("low", "Менее"),
                        ("normal", "По умолч."),
                        ("high", "Более"),
                    ]
                ]
            )

            kb.row(
                InlineKeyboardButton(
                    text="📝 Изменить инструкции",
                    callback_data=SettingsCB.input("custom_instructions"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text="👤 Изменить псевдоним",
                    callback_data=SettingsCB.input("alias"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{'✅' if p.adaptive_mode_enabled else '❌'} Адаптивный режим",
                    callback_data=SettingsCB.toggle("adaptive_mode_enabled"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text=f"{'✅' if s.anti_ai_enabled else '❌'} Anti-AI защита",
                    callback_data=SettingsCB.toggle("anti_ai_enabled"),
                )
            )
            kb.row(
                InlineKeyboardButton(
                    text="⚙️ Режим Anti-AI",
                    callback_data=SettingsCB.noop("anti_ai_mode"),
                )
            )
            kb.row(
                *[
                    InlineKeyboardButton(
                        text=("\u2022 " if s.anti_ai_mode == mode else "") + label,
                        callback_data=SettingsCB.choose("anti_ai_mode", mode),
                    )
                    for mode, label in [
                        ("off", "Выкл"),
                        ("log", "Лог"),
                        ("fix", "Исправлять"),
                    ]
                ]
            )
            kb.row(
                InlineKeyboardButton(
                    text="↩ Сбросить к базовым",
                    callback_data=SettingsCB.persona_reset(),
                )
            )
            kb.row(*_back_row())

        else:
            text = "Раздел не найден."
            kb.row(*_back_row())

    return text, kb.as_markup()


# =====================================================================
#  FSM ENTRY CALLBACKS (cb_input_*)
# =====================================================================


@router.callback_query(F.data == SettingsCB.input("openai_key"))
async def cb_input_openai(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_openai_key)
    await callback.message.answer(
        "Пришли OpenAI API-ключ. Проверю и сохраню. /cancel — отмена.\n\n"
        "💡 Можно несколько ключей через запятую."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("gemini_key"))
async def cb_input_gemini(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_gemini_key)
    await callback.message.answer(
        "Пришли Gemini API-ключ. Проверю и сохраню. /cancel — отмена.\n\n"
        "💡 Можно несколько ключей через запятую."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("mistral_key"))
async def cb_input_mistral(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_mistral_key)
    await callback.message.answer(
        "Пришли Mistral API-ключ. Проверю и сохраню. /cancel — отмена.\n\n"
        "💡 Можно несколько ключей через запятую."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("cloudflare_key"))
async def cb_input_cloudflare(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_cloudflare_key)
    await callback.message.answer(
        "Пришли Cloudflare API-ключ. Проверю и сохраню. /cancel — отмена.\n\n"
        "💡 Можно несколько ключей через запятую."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("deepseek_key"))
async def cb_input_deepseek(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_deepseek_key)
    await callback.message.answer(
        "Пришли DeepSeek API-ключ. Проверю и сохраню. /cancel — отмена.\n\n"
        "💡 Можно несколько ключей через запятую."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("grok_key"))
async def cb_input_grok(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_grok_key)
    await callback.message.answer(
        "Пришли Grok API-ключ. Проверю и сохраню. /cancel — отмена.\n\n"
        "💡 Можно несколько ключей через запятую."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("mimo_key"))
async def cb_input_mimo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_mimo_key)
    await callback.message.answer(
        "Пришли MiMo API-ключ. Проверю и сохраню. /cancel — отмена.\n\n"
        "💡 Можно несколько ключей через запятую."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("groq_key"))
async def cb_input_groq(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_groq_key)
    await callback.message.answer(
        "Пришли Groq API-ключ. Проверю и сохраню. /cancel — отмена.\n\n"
        "💡 Можно несколько ключей через запятую."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("deepgram_key"))
async def cb_input_deepgram(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_deepgram_key)
    await callback.message.answer(
        "🔑 Введите ваш Deepgram API Key:\n\n"
        "Получить ключ: https://console.deepgram.com/\n"
        "/cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("assemblyai_key"))
async def cb_input_assemblyai(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_assemblyai_key)
    await callback.message.answer(
        "🔑 Введите ваш AssemblyAI API Key:\n\n"
        "Получить ключ: https://www.assemblyai.com/\n"
        "/cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("custom_name"))
async def cb_input_custom_name(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_custom_name)
    await callback.message.answer(
        "➕ <b>Свой провайдер</b>\n\n"
        "Шаг 1/4: Пришли название провайдера (например: <code>Local LLM</code>).\n"
        "/cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("digest_time"))
async def cb_input_digest(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_digest_time)
    await callback.message.answer(
        "Введи время в формате <code>HH:MM</code> (UTC). /cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("auto_reply_text"))
async def cb_input_auto_reply(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_auto_reply_text)
    await callback.message.answer(
        "Пришли новый текст автоответа. Будет отправляться, когда ты оффлайн "
        "(в режиме «заготовка»). /cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("auto_sync_interval"))
async def cb_input_sync_interval(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_sync_interval)
    await callback.message.answer(
        "Введи интервал в секундах (минимум 30). Например: 3600 = 1 час, 7200 = 2 часа. /cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("news_time"))
async def cb_input_news_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_news_time)
    await callback.message.answer(
        "Введи время утренних авто-новостей в <code>HH:MM</code> (UTC). /cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.noop("news_topics"))
async def cb_noop_news_topics(callback: CallbackQuery) -> None:
    await callback.answer("Открой /news_topics в меню команд", show_alert=True)


# ── Тихие часы ──


@router.callback_query(F.data == SettingsCB.input("quiet_hours_start"))
async def cb_input_quiet_hours_start(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await callback.message.answer(
        "Введи время начала тихих часов (HH:MM, например 23:00):"
    )
    await state.set_state(SettingsStates.waiting_quiet_hours_start)
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("quiet_hours_end"))
async def cb_input_quiet_hours_end(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.answer(
        "Введи время конца тихих часов (HH:MM, например 07:00):"
    )
    await state.set_state(SettingsStates.waiting_quiet_hours_end)
    await callback.answer()


# ── Личность ──


@router.callback_query(F.data == SettingsCB.input("alias"))
async def cb_input_alias(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_alias)
    await callback.message.answer(
        "👤 Как к тебе обращаться?\n\n"
        "Напиши имя или прозвище (например: <i>Миша, Александр Петрович, шеф</i>). "
        "Бот будет использовать это обращение в общении.\n"
        "/cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.input("custom_instructions"))
async def cb_input_custom_instructions(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(SettingsStates.waiting_custom_instructions)
    await callback.message.answer(
        "📝 <b>Пользовательские инструкции</b>\n\n"
        "Напиши свободный текст — как бот должен себя вести, что знать, "
        "какие темы избегать, и т.д.\n\n"
        "Например: <i>«Не используй англицизмы. Всегда проверяй факты. "
        "Перед ответом на сложный вопрос предупреждай что думаешь.»</i>\n\n"
        "/cancel — отмена."
    )
    await callback.answer()


# =====================================================================
#  FSM STEP HANDLERS
# =====================================================================


# ── MiMo key ──


@router.message(SettingsStates.waiting_mimo_key)
async def step_mimo_key(message: Message, state: FSMContext) -> None:
    """Сохраняет MiMo API ключ, затем спрашивает регион."""
    raw = (message.text or "").strip()
    if raw in ("/cancel", "/back", "/menu"):
        await state.clear()
        text, kb = await _render_menu(message.from_user.id)
        await message.answer("❌ Ввод ключа отменён.")
        await message.answer(text, reply_markup=kb)
        return
    if not raw:
        await message.answer("Пустой ключ. Повтори или /cancel.")
        return
    parts = [k.strip() for k in raw.split(",") if k.strip()]
    if not parts:
        await message.answer("Нет ни одного непустого ключа. Повтори или /cancel.")
        return
    try:
        await message.delete()
    except TelegramError:
        logger.warning("failed to delete message with mimo key")
    if not await MiMoProvider(parts[0]).validate_key():
        await message.answer("❌ Ключ не работает. Повтори или /cancel.")
        return
    await state.update_data(mimo_key=",".join(parts))
    await state.set_state(SettingsStates.waiting_mimo_region)
    kb = InlineKeyboardBuilder()
    for region_key, region_url in MIMO_REGIONS.items():
        label = {"eu": "🇪🇺 EU", "us": "🇺🇸 US", "asia": "🌏 Asia"}.get(
            region_key, region_key.upper()
        )
        kb.button(text=label, callback_data=f"set:mimo_region:{region_key}")
    kb.button(text="⏭ Пропустить (Asia)", callback_data="set:mimo_region:skip")
    kb.adjust(2)
    await message.answer(
        "🌍 <b>Выбери регион MiMo API:</b>\n\n"
        "MiMo имеет региональные endpoint'ы. Выбери ближайший к тебе регион "
        "для минимальной задержки.\n\n"
        "• 🇪🇺 EU — Европа\n"
        "• 🇺🇸 US — США\n"
        "• 🌏 Asia — Азия (по умолчанию)\n\n"
        "/cancel — отмена.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("set:mimo_region:"))
async def cb_mimo_region(callback: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает выбор региона MiMo — сохраняет ключ с endpoint."""
    region_raw = callback.data.split(":", 2)[2]
    await callback.answer()

    if region_raw == "skip":
        endpoint = MIMO_REGIONS["asia"]
        region_label = "Asia (по умолчанию)"
    else:
        endpoint = MIMO_REGIONS.get(region_raw, MIMO_REGIONS["asia"])
        region_label = {"eu": "EU", "us": "US", "asia": "Asia"}.get(
            region_raw, region_raw
        )

    data = await state.get_data()
    mimo_key = data.get("mimo_key", "")
    if not mimo_key:
        await callback.message.answer(
            "❌ Ключ не найден. Начни заново: /settings → API-ключи → MiMo key."
        )
        await state.clear()
        return

    parts = [k.strip() for k in mimo_key.split(",") if k.strip()]
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        await upsert_api_key(session, owner, "mimo", mimo_key)
        for i, single_key in enumerate(parts):
            slot, _is_new = await add_key_slot(
                session,
                owner,
                "mimo",
                single_key,
                purpose="main",
                priority=i,
                endpoint=endpoint,
            )
            if not slot.endpoint:
                slot.endpoint = endpoint
        await session.flush()
        total = await _count_slots_for_provider(session, owner, "mimo")
    await state.clear()
    count = len(parts)
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Ещё ключ", callback_data=SettingsCB.input("mimo_key"))
    kb.button(text="✅ Назад", callback_data="set:done:key")
    kb.adjust(2)
    if callback.message:
        await callback.message.edit_text(
            f"✅ Сохранено MiMo ключей: {count} (регион: {region_label}).\n"
            f"🔑 В базе MiMo ключей: {total}.\n\n"
            "Добавить ещё?",
            reply_markup=kb.as_markup(),
        )


# ── Custom provider FSM (4 шага) ──


@router.message(SettingsStates.waiting_custom_name)
async def step_custom_name(message: Message, state: FSMContext) -> None:
    """Шаг 1/4: название провайдера."""
    name = (message.text or "").strip()
    if name == "/cancel":
        await state.clear()
        text, kb = await _render_menu(message.from_user.id)
        await message.answer("🚫 Отменено.")
        await message.answer(text, reply_markup=kb)
        return
    if not name:
        await message.answer("Введи название. /cancel — отмена.")
        return
    await state.update_data(custom_name=name)
    await state.set_state(SettingsStates.waiting_custom_endpoint)
    await message.answer(
        f"✅ Название: <b>{sanitize_html(name)}</b>\n\n"
        "Шаг 2/4: Пришли endpoint (базовый URL API).\n"
        "Например: <code>https://api.openai.com/v1</code>\n"
        "/cancel — отмена."
    )


@router.message(SettingsStates.waiting_custom_endpoint)
async def step_custom_endpoint(message: Message, state: FSMContext) -> None:
    """Шаг 2/4: endpoint."""
    endpoint = (message.text or "").strip()
    if endpoint == "/cancel":
        await state.clear()
        text, kb = await _render_menu(message.from_user.id)
        await message.answer("🚫 Отменено.")
        await message.answer(text, reply_markup=kb)
        return
    if not endpoint:
        await message.answer("Введи URL. /cancel — отмена.")
        return
    if not endpoint.startswith("https://") and not endpoint.startswith("http://"):
        await message.answer("❌ URL должен начинаться с https:// или http://")
        return
    await state.update_data(custom_endpoint=endpoint)
    await state.set_state(SettingsStates.waiting_custom_key)
    await message.answer(
        f"✅ Endpoint: <code>{sanitize_html(endpoint)}</code>\n\n"
        "Шаг 3/4: Пришли API-ключ.\n"
        "💡 Можно несколько ключей через запятую.\n"
        "/cancel — отмена."
    )


@router.message(SettingsStates.waiting_custom_key)
async def step_custom_key(message: Message, state: FSMContext) -> None:
    """Шаг 3/4: API-ключ + валидация."""
    raw = (message.text or "").strip()
    if raw in ("/cancel", "/back", "/menu"):
        await state.clear()
        text, kb = await _render_menu(message.from_user.id)
        await message.answer("❌ Ввод ключа отменён.")
        await message.answer(text, reply_markup=kb)
        return
    if not raw:
        await message.answer("Пустой ключ. Повтори или /cancel.")
        return
    parts = [k.strip() for k in raw.split(",") if k.strip()]
    if not parts:
        await message.answer("Нет ни одного непустого ключа. Повтори или /cancel.")
        return
    data = await state.get_data()
    endpoint = data.get("custom_endpoint", "")
    try:
        await message.delete()
    except TelegramError:
        logger.warning("failed to delete message with custom key")
    try:
        valid = await CustomProvider(parts[0], endpoint=endpoint).validate_key()
    except (ValueError, APIConnectionError):
        valid = False
    if not valid:
        await message.answer(
            "❌ Ключ не работает или endpoint недоступен. Повтори или /cancel."
        )
        return
    await state.update_data(custom_key=",".join(parts))
    await state.set_state(SettingsStates.waiting_custom_models)
    await message.answer(
        "✅ Ключ работает!\n\n"
        "Шаг 4/4: Пришли модели через запятую.\n"
        "Например: <code>gpt-4, gpt-3.5-turbo, my-model</code>\n"
        "💡 Каждая модель будет доступна для всех задач.\n"
        "/cancel — отмена."
    )


@router.message(SettingsStates.waiting_custom_models)
async def step_custom_models(message: Message, state: FSMContext) -> None:
    """Шаг 4/4: модели — создаёт слоты в БД."""
    raw_models = (message.text or "").strip()
    if raw_models == "/cancel":
        await state.clear()
        text, kb = await _render_menu(message.from_user.id)
        await message.answer("🚫 Отменено.")
        await message.answer(text, reply_markup=kb)
        return
    if not raw_models:
        await message.answer("Введи хотя бы одну модель. /cancel — отмена.")
        return
    models = [m.strip() for m in raw_models.split(",") if m.strip()]
    data = await state.get_data()
    name = data.get("custom_name", "custom")
    endpoint = data.get("custom_endpoint", "")
    key = data.get("custom_key", "")
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        for model in models:
            await add_key_slot(
                session,
                owner,
                provider="custom",
                purpose="main",
                model=model,
                label=f"{name}:{model}",
                endpoint=endpoint,
                key=key,
            )
        total = await _count_slots_for_provider(session, owner, "custom")
    await state.clear()
    count = len(models)
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Ещё провайдер", callback_data=SettingsCB.input("custom_name"))
    kb.button(text="✅ Назад", callback_data="set:done:key")
    kb.adjust(2)
    await message.answer(
        f"✅ Провайдер <b>{sanitize_html(name)}</b> добавлен!\n"
        f"Моделей: {count} · Всего custom ключей: {total}.\n\n"
        "Добавить ещё?",
        reply_markup=kb.as_markup(),
    )


# ── Digest time ──


@router.message(SettingsStates.waiting_digest_time)
async def step_digest_time(message: Message, state: FSMContext) -> None:
    hm = (message.text or "").strip()
    if not HM_RE.match(hm):
        await message.answer(
            "Формат HH:MM, например <code>06:30</code>. Повтори или /cancel."
        )
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.digest_time = hm
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(message.from_user.id)
    await state.clear()
    await message.answer(f"✅ Время дайджеста: <b>{hm} UTC</b>.")


# ── News time ──


@router.message(SettingsStates.waiting_news_time)
async def step_news_time(message: Message, state: FSMContext) -> None:
    hm = (message.text or "").strip()
    if not HM_RE.match(hm):
        await message.answer(
            "Формат HH:MM, например <code>07:30</code>. Повтори или /cancel."
        )
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.news_digest_time = hm
        tz = owner.settings.timezone
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(message.from_user.id)
    await state.clear()
    await message.answer(f"✅ Время авто-новостей: <b>{hm}</b> · {tz_short(tz)}.")


# ── Auto-reply text ──


@router.message(SettingsStates.waiting_auto_reply_text)
async def step_auto_reply_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст. Повтори или /cancel.")
        return
    if len(text) > 1000:
        await message.answer(
            "Слишком длинно (макс. 1000 символов). Повтори или /cancel."
        )
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.auto_reply_text = text
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(message.from_user.id)
    await state.clear()
    await message.answer(
        sanitize_html(f"✅ Текст автоответа сохранён:\n<i>«{text}»</i>")
    )


# ── Timezone ──


@router.message(SettingsStates.waiting_timezone)
async def step_timezone(message: Message, state: FSMContext) -> None:
    tz_value = (message.text or "").strip()
    if not is_valid_tz(tz_value):
        await message.answer(
            "Не нашёл такой TZ. Используй IANA-формат, например <code>Europe/Moscow</code>. "
            "Список: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones — "
            "колонка «TZ identifier». /cancel — отмена."
        )
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.timezone = tz_value
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(message.from_user.id)
    await state.clear()
    await message.answer(f"✅ Часовой пояс: <b>{tz_short(tz_value)}</b>")


# ── Sync interval ──


@router.message(SettingsStates.waiting_sync_interval)
async def step_sync_interval(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val.isdigit():
        await message.answer("Ожидаю число (секунд). Повтори или /cancel.")
        return
    secs = max(30, int(val))
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.auto_sync_interval_sec = secs
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(message.from_user.id)
    await state.clear()
    await message.answer(f"✅ Интервал авто-синка: <b>{secs} сек</b>")


# ── Quiet hours ──


@router.message(SettingsStates.waiting_quiet_hours_start)
async def step_quiet_hours_start(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not HM_RE.match(text):
        await message.answer("❌ Неверный формат. Введи HH:MM (например 23:00):")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.quiet_hours_start = text
        await session.flush()
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(message.from_user.id)
    await state.clear()
    await message.answer(f"✅ Тихие часы начало: <b>{text}</b>")


@router.message(SettingsStates.waiting_quiet_hours_end)
async def step_quiet_hours_end(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not HM_RE.match(text):
        await message.answer("❌ Неверный формат. Введи HH:MM (например 07:00):")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.quiet_hours_end = text
        await session.flush()
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(message.from_user.id)
    await state.clear()
    await message.answer(f"✅ Тихие часы конец: <b>{text}</b>")


# ── Alias ──


@router.message(SettingsStates.waiting_alias)
async def step_alias(message: Message, state: FSMContext) -> None:
    alias = (message.text or "").strip()
    if not alias:
        await message.answer("Пустое обращение. Повтори или /cancel.")
        return
    if len(alias) > 64:
        await message.answer("Слишком длинное (макс. 64 символа). Повтори или /cancel.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        p = await get_persona(session, owner)
        p.alias = alias
        await session.flush()
    from src.core.context_cache import invalidate as cache_invalidate

    await cache_invalidate(f"persona:{message.from_user.id}")
    await state.clear()
    await message.answer(sanitize_html(f"✅ Обращение сохранено: <b>{alias}</b>"))


# ── Custom instructions ──


@router.message(SettingsStates.waiting_custom_instructions)
async def step_custom_instructions(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст. Повтори или /cancel.")
        return
    if len(text) > 2000:
        await message.answer(
            "Слишком длинный текст (макс. 2000 символов). Повтори или /cancel."
        )
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        p = await get_persona(session, owner)
        p.custom_instructions = text
        await session.flush()
    from src.core.context_cache import invalidate as cache_invalidate

    await cache_invalidate(f"persona:{message.from_user.id}")
    await state.clear()
    await message.answer(
        sanitize_html(
            "✅ Инструкции сохранены!\n\n"
            f"<i>«{text[:300]}{'…' if len(text) > 300 else ''}»</i>"
        )
    )


# =====================================================================
#  KEY HANDLER REGISTRATIONS (via make_key_handler factory)
# =====================================================================

step_openai_key = make_key_handler(
    SettingsStates.waiting_openai_key,
    "openai",
    OpenAIProvider,
    provider_label="OpenAI",
)
router.message.register(step_openai_key, SettingsStates.waiting_openai_key)

step_gemini_key = make_key_handler(
    SettingsStates.waiting_gemini_key,
    "gemini",
    GeminiProvider,
)
router.message.register(step_gemini_key, SettingsStates.waiting_gemini_key)

step_mistral_key = make_key_handler(
    SettingsStates.waiting_mistral_key,
    "mistral",
    MistralProvider,
)
router.message.register(step_mistral_key, SettingsStates.waiting_mistral_key)

step_cloudflare_key = make_key_handler(
    SettingsStates.waiting_cloudflare_key,
    "cloudflare",
    CloudflareProvider,
    validation_error_msg="❌ Ключ не работает. Проверь API Token и CLOUDFLARE_ACCOUNT_ID в .env. /cancel.",
)
router.message.register(step_cloudflare_key, SettingsStates.waiting_cloudflare_key)

step_deepseek_key = make_key_handler(
    SettingsStates.waiting_deepseek_key,
    "deepseek",
    DeepSeekProvider,
    provider_label="DeepSeek",
)
router.message.register(step_deepseek_key, SettingsStates.waiting_deepseek_key)

step_grok_key = make_key_handler(
    SettingsStates.waiting_grok_key,
    "grok",
    GrokProvider,
)
router.message.register(step_grok_key, SettingsStates.waiting_grok_key)

step_groq_key = make_key_handler(
    SettingsStates.waiting_groq_key,
    "groq",
    GroqProvider,
)
router.message.register(step_groq_key, SettingsStates.waiting_groq_key)

step_deepgram_key = make_key_handler(
    SettingsStates.waiting_deepgram_key,
    "deepgram",
    category="stt",
)
router.message.register(step_deepgram_key, SettingsStates.waiting_deepgram_key)

step_assemblyai_key = make_key_handler(
    SettingsStates.waiting_assemblyai_key,
    "assemblyai",
    category="stt",
    provider_label="AssemblyAI",
)
router.message.register(step_assemblyai_key, SettingsStates.waiting_assemblyai_key)
