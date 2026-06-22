"""FSM input handlers for settings — key entry, time, text, etc.

SRP: all cb_input_* callbacks, step handlers, and make_key_handler registrations.
"""

import logging

from aiogram import F
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.callbacks import SettingsCB
from src.bot.handlers._key_input import make_key_handler
from src.bot.handlers.settings_menu import _render_menu
from src.bot.handlers.settings_router import router
from src.bot.handlers.settings_service import _count_slots_for_provider
from src.bot.states import SettingsStates
from src.core.infra.text_sanitizer import sanitize_html
from src.core.infra.timeutil import HM_RE, is_valid_tz, tz_short
from src.db.repo import (
    add_key_slot,
    get_or_create_user,
    get_persona,
    upsert_api_key,
)
from src.db.session import get_session
from src.llm.cloudflare_provider import CloudflareProvider
from src.llm.custom_provider import CustomProvider
from src.llm.deepseek_provider import DeepSeekProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.grok_provider import GrokProvider
from src.llm.groq_provider import GroqProvider
from src.llm.mimo_provider import MIMO_REGIONS, MiMoProvider
from src.llm.mistral_provider import MistralProvider
from src.llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

# Примечание (issue #3): если openai не установлен, APIConnectionError недоступен.
try:
    import openai as _openai
except ImportError:  # pragma: no cover
    _openai = None


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


@router.callback_query(F.data == SettingsCB.input("news_digest_time"))
async def cb_input_news_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_news_time)
    await callback.message.answer(
        "Введи время утренних авто-новостей в <code>HH:MM</code> (UTC). /cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == SettingsCB.noop("news_topics"))
async def cb_noop_news_topics(callback: CallbackQuery) -> None:
    await callback.answer("Открой /news_topics в меню команд", show_alert=True)


# ponytail: quiet_hours FSM handlers removed — NL path via free_text_settings.py
# (_exec_set_quiet_hours) handles DB write + cache invalidation directly.
# Enforcement is in auto_reply_decision.py::decide().


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
    except TelegramAPIError:
        logger.warning("failed to delete message with mimo key")
    if not await MiMoProvider(parts[0]).validate_key():
        await message.answer("❌ Ключ не работает. Повтори или /cancel.")
        return
    await state.update_data(mimo_key=",".join(parts))
    await state.set_state(SettingsStates.waiting_mimo_region)
    kb = InlineKeyboardBuilder()
    for region_key in MIMO_REGIONS:
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


@router.message(SettingsStates.waiting_mimo_region)
async def step_mimo_region_text(message: Message) -> None:
    """Text input when MiMo region button expected."""
    await message.answer("🌍 Выбери регион кнопкой выше. /cancel — отмена.")


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
    except TelegramAPIError:
        logger.warning("failed to delete message with custom key")
    try:
        valid = await CustomProvider(parts[0], endpoint=endpoint).validate_key()
    except ValueError:
        valid = False
    except Exception as _e:
        if _openai is not None and isinstance(_e, _openai.APIConnectionError):
            valid = False
        else:
            raise
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


# ponytail: quiet_hours step handlers removed — NL path handles this directly.


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
