"""Handler for /start and the onboarding wizard for first-time users."""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from src.bot.callbacks import OnboardingCB, SettingsCB
from src.bot.filters import OwnerOnly, is_onboarded
from src.bot.handlers.greeting import generate_personalized_greeting
from src.bot.states import CustomProviderStates, OnboardingStates
from src.db.models._contacts import Contact
from src.db.models._learning import AdaptivePersona
from src.db.models._memory import Memory
from src.db.repo import add_key_slot, get_or_create_user, upsert_api_key
from src.db.session import get_session
from src.core.infra.key_guard import safe_str
from src.core.infra.provider_names import provider_display_name
from src.core.infra.timeutil import TZ_PRESETS, is_valid_tz, tz_short
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.cloudflare_provider import CloudflareProvider
from src.llm.deepseek_provider import DeepSeekProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.grok_provider import GrokProvider
from src.llm.groq_provider import GroqProvider
from src.llm.mimo_provider import MIMO_REGIONS, MiMoProvider
from src.llm.mistral_provider import MistralProvider
from src.llm.openai_provider import OpenAIProvider
from src.llm.openrouter_provider import OpenRouterProvider

logger = logging.getLogger(__name__)

router = Router(name="start")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


# ─── helpers ──────────────────────────────────────────────────────────


WELCOME = (
    "👋 <b>Привет! Я твой AI-ассистент для Telegram</b>\n\n"
    "<b>Аккаунт</b>\n"
    "🔑 /login — подключить Telegram-аккаунт (api_id, api_hash, телефон, код, 2FA)\n"
    "🚪 /logout — удалить сохранённую сессию\n"
    "🔄 /sync — обновить список контактов из диалогов\n\n"
    "<b>Настройки</b>\n"
    "⚙️ /settings — авто-ответ, выбор LLM, API-ключи\n\n"
    "<b>Работа с чатами</b>\n"
    "💬 /chat &lt;имя&gt; — саммари, задачи, черновик ответа, «где остановились»\n"
    "⏪ /catchup &lt;имя&gt; — где мы остановились + черновик ответа\n"
    "🔍 /search &lt;текст&gt; — поиск по проиндексированным сообщениям\n"
    "📇 /index &lt;имя&gt; — проиндексировать чат для семантического поиска\n"
    "📤 /send &lt;инструкция&gt; — «скажи Оле, что созвон в 8» (с подтверждением)\n\n"
    "<b>Новости</b>\n"
    "📰 /news &lt;тема&gt; [--hours=24] — дайджест из подписанных каналов\n"
    "📡 /news_channels — отметить каналы-источники\n"
    "🏷 /news_topics — темы для утренних авто-новостей\n\n"
    "<b>Память и фичи</b>\n"
    "📋 /todos — открытые обещания (мои и мне)\n"
    "☀️ /digest [now|on|off|at HH:MM] — утренний дайджест\n"
    "🎭 /style &lt;имя&gt; — пересчитать профиль моего стиля общения с этим контактом\n"
    "🧠 /memory — показать память (факты о контактах)\n"
    "📬 /threads — активные переписки\n\n"
    "📖 /help — эта подсказка\n\n"
    "<b>Можно писать своими словами:</b>\n"
    "<i>• «Напиши Ивану что задержусь на 10 минут»</i> → отправка сообщения\n"
    "<i>• «Что нового в чате с Петей?»</i> → саммари переписки\n"
    "<i>• «Напомни завтра в 10 про отчёт»</i> → напоминание\n"
    "<i>• «Где мы остановились с Машей?»</i> → catchup\n"
    "<i>• «Запомни: у Насти ДР 15 июня»</i> → память\n"
    "<i>• «Сделай краткую выжимку новостей про AI»</i> → дайджест\n"
    "<i>• «Ответь Игорю: давай в среду»</i> → черновик ответа\n"
    "<i>• «Какие у меня задачи?»</i> → список обещаний\n"
)


# ─── existing greeting (returning users) ──────────────────────────────


def _greeting_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 /help", callback_data="nav:help"),
                InlineKeyboardButton(text="⚙️ /settings", callback_data="nav:settings"),
                InlineKeyboardButton(text="💬 /chat", callback_data="nav:chat"),
            ],
            [
                InlineKeyboardButton(text="📬 Треды", callback_data="thread:refresh"),
                InlineKeyboardButton(text="📋 Задачи", callback_data="nav:todos"),
                InlineKeyboardButton(text="🧠 Память", callback_data="nav:memory"),
            ],
            [
                InlineKeyboardButton(
                    text="🎭 Личность", callback_data=SettingsCB.section("personality")
                ),
            ],
        ]
    )


# ─── /start ────────────────────────────────────────────────────────────


@router.message(Command("start"), StateFilter(default_state))
async def cmd_start(message: Message) -> None:
    """Точка входа. Если пользователь уже прошёл онбординг — обычное приветствие."""
    tg_id = message.from_user.id

    if await is_onboarded(tg_id):
        await _show_regular_greeting(message)
        return

    # Начинаем онбординг — шаг 1: Welcome
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Начать", callback_data=OnboardingCB.start()
                ),
            ],
        ]
    )
    await message.answer(
        "👋 <b>Привет! Я твой AI-ассистент для Telegram</b>\n\n"
        "Давай настроим всё за 5 шагов, чтобы я мог полноценно работать.\n\n"
        "<b>Что настроим:</b>\n"
        "1️⃣ 🔑 Подключим твой Telegram-аккаунт\n"
        "2️⃣ 🤖 Добавим API-ключ для ИИ\n"
        "3️⃣ 🕐 Выберем часовой пояс\n"
        "4️⃣ 📱 Настроим синхронизацию чатов\n\n"
        "Готов? 👇",
        reply_markup=kb,
    )


@router.message(Command("start"), StateFilter(OnboardingStates))
async def cmd_start_during_onboarding(message: Message) -> None:
    """Если пользователь нажал /start во время онбординга — показываем текущий шаг."""
    await message.answer(
        "🔄 Ты уже проходишь настройку. Напиши /cancel чтобы выйти, "
        "или продолжай — я жду твой ответ на текущий шаг 😊"
    )


async def _show_regular_greeting(message: Message) -> None:
    """Полное приветствие для вернувшегося пользователя."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        has_session = owner.session is not None
        llm = provider_display_name(owner.settings.llm_provider, pretty_openrouter=True)
        tz = tz_short(owner.settings.timezone) if owner.settings.timezone else "UTC"

        # Проверяем, новый ли пользователь (нет persona или 0 взаимодействий)
        from src.db.models._learning import AdaptivePersona
        from sqlalchemy import select

        stmt = select(AdaptivePersona).where(AdaptivePersona.user_id == owner.id)
        result = await session.execute(stmt)
        persona = result.scalar_one_or_none()

    is_new = (persona is None) or (persona.total_interactions == 0)

    # Персонализированный контекст: память + инбокс + задачи
    try:
        personalized = await generate_personalized_greeting(message.from_user.id)
    except Exception:
        logger.debug("personalized greeting failed for /start", exc_info=True)
        personalized = ""
    context_section = ""
    if personalized:
        context_section = f"{personalized}\n\n"

    # ── Ambient Intelligence (Phase 6) ——
    from src.config import settings as _settings

    if _settings.ambient_intelligence_enabled:
        try:
            from src.bot.ambient import AmbientIntelligence

            ambient = AmbientIntelligence(message.bot)
            ctx: dict = {
                "last_active_at": owner.last_seen_online,
                "active_tasks": [],  # ponytail: recall() adds latency; empty for greeting
                "recent_insights": [],  # ponytail: ditto
            }
            await ambient.check_and_notify(message.from_user.id, ctx)
        except Exception:
            logger.debug("ambient check failed for /start", exc_info=True)

    auth_status = "Ты авторизован ✅" if has_session else "Не авторизован ❌"

    header = (
        f"👋 <b>Привет! Я твой AI-ассистент для Telegram</b>\n\n"
        f"<b>📊 Текущий статус</b>\n"
        f"{auth_status}\n"
        f"🤖 LLM: {llm}\n"
        f"🕐 Часовой пояс: {tz}\n\n"
    )

    onboarding_text = ""
    if is_new:
        onboarding_text = (
            "\n\n🎭 <b>Хочешь настроить личность бота под себя?</b>\n"
            "Я могу общаться в разных стилях: профессионально, дружелюбно, "
            "игриво, лаконично и даже с сарказмом!\n\n"
            "Нажми кнопку ниже чтобы настроить."
        )

    await message.answer(
        header + context_section + WELCOME + onboarding_text,
        reply_markup=_greeting_kb(),
    )


# ─── /help ─────────────────────────────────────────────────────────────


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    from src.bot.command_registry import get_registry

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        auth_status = "✅" if owner.session else "❌"
        llm = provider_display_name(owner.settings.llm_provider, pretty_openrouter=True)
    header = (
        f"📖 <b>Помощь по командам</b>\n"
        f"{'Ты авторизован' if auth_status == '✅' else 'Не авторизован'} {auth_status} · "
        f"LLM: {llm}\n"
    )
    help_text = get_registry().format_help()
    await message.answer(header + "\n" + help_text)


# ─── navigation callbacks (existing) ───────────────────────────────────


@router.callback_query(F.data.startswith("nav:"))
async def cb_nav(callback: CallbackQuery) -> None:
    """Обработка навигационных кнопок."""
    target = callback.data.split(":", 1)[1]
    mapping = {
        "help": "/help",
        "settings": "/settings",
        "chat": "/chat",
        "todos": "/todos",
        "memory": "/memory",
        "threads": "/threads",
    }
    cmd = mapping.get(target, f"/{target}")
    await callback.answer(f"Выполняю {cmd}")
    if callback.message:
        await callback.message.edit_text(
            f"🔄 Нажми в поле ввода: <code>{cmd}</code> и отправь."
        )


@router.callback_query(F.data == "persona:skip_onboarding")
async def cb_skip_onboarding(callback: CallbackQuery) -> None:
    """Пользователь пропустил onboarding личности."""
    await callback.answer(
        "Ок, настройки можно изменить в любой момент в /settings → 🎭 Личность"
    )
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            logger.debug("Non-critical error", exc_info=True)


# ═══════════════════════════════════════════════════════════════════════
# ONBOARDING WIZARD
# ═══════════════════════════════════════════════════════════════════════

# ─── Step 1: Welcome → "🚀 Начать" callback ───────────────────────────


@router.callback_query(F.data == OnboardingCB.start())
async def cb_onboarding_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь нажал «Начать» — переходим к шагу авторизации."""
    await state.set_state(OnboardingStates.waiting_login)

    if callback.message is None:
        await callback.answer("Сообщение недоступно.")
        return
    await callback.answer()

    # Убираем кнопку "Начать"
    try:
        await callback.message.edit_text(
            "👋 <b>Привет! Я твой AI-ассистент для Telegram</b>\n\n"
            "Давай настроим всё за 5 шагов 🚀"
        )
    except Exception:
        logger.debug("Non-critical error", exc_info=True)

    await _send_login_step(callback.message.chat.id, callback.bot)


async def _send_login_step(chat_id: int, bot, state: FSMContext | None = None) -> None:
    """Отправляет сообщение шага «Авторизация»."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔑 /login", callback_data=OnboardingCB.hint_login()
                ),
            ],
        ]
    )
    await bot.send_message(
        chat_id,
        "🚀 <b>Шаг 1/4 — Авторизация</b>\n\n"
        "Подключи свой Telegram-аккаунт командой /login",
        reply_markup=kb,
    )


@router.callback_query(F.data == OnboardingCB.hint_login())
async def cb_onboarding_hint_login(callback: CallbackQuery) -> None:
    """Подсказка как отправить /login."""
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🔑 Просто отправь в чат команду:\n\n"
            "<code>/login</code>\n\n"
            "И следуй инструкциям бота. После успешного входа "
            "я продолжу настройку автоматически."
        )


@router.message(OnboardingStates.waiting_login)
async def step_onboarding_login(message: Message, state: FSMContext) -> None:
    """Пользователь что-то отправил на шаге login (не /login)."""
    # Проверяем, может уже есть сессия
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        has_session = owner.session is not None

    if has_session:
        # Уже авторизован — переходим к следующему шагу
        await state.set_state(OnboardingStates.waiting_llm_key)
        await _send_llm_key_step(message.chat.id, message.bot)
        return

    await message.answer(
        "🔑 Нажми /login или нажми на кнопку выше, чтобы авторизоваться.\n"
        "/cancel — отменить настройку."
    )


# ─── Step 2: LLM Key ─────────────────────────────────────────────────


async def _send_llm_key_step(chat_id: int, bot) -> None:
    """Отправляет сообщение шага «Подключи мозг» с выбором провайдера."""
    text = (
        "🧠 <b>Шаг 2/4 — подключи мозг</b>\n\n"
        "Выбери провайдера, которому доверяешь. "
        "Можно добавить несколько — бот будет переключаться при ошибках.\n\n"
        "<b>💬 Чат-модели:</b>"
    )
    kb = InlineKeyboardBuilder()
    # Row 1: OpenAI, Gemini
    kb.row(
        InlineKeyboardButton(
            text="🤖 OpenAI", callback_data=OnboardingCB.provider("openai")
        ),
        InlineKeyboardButton(
            text="🔮 Gemini", callback_data=OnboardingCB.provider("gemini")
        ),
    )
    # Row 2: Mistral, Anthropic
    kb.row(
        InlineKeyboardButton(
            text="🌪️ Mistral", callback_data=OnboardingCB.provider("mistral")
        ),
        InlineKeyboardButton(
            text="🧬 Anthropic", callback_data=OnboardingCB.provider("anthropic")
        ),
    )
    # Row 3: DeepSeek, Grok
    kb.row(
        InlineKeyboardButton(
            text="🐋 DeepSeek", callback_data=OnboardingCB.provider("deepseek")
        ),
        InlineKeyboardButton(
            text="⚡ Grok (xAI)", callback_data=OnboardingCB.provider("grok")
        ),
    )
    # Row 4: Groq, MiMo
    kb.row(
        InlineKeyboardButton(
            text="🚀 Groq", callback_data=OnboardingCB.provider("groq")
        ),
        InlineKeyboardButton(
            text="📱 MiMo (Xiaomi)", callback_data=OnboardingCB.provider("mimo")
        ),
    )
    # Row 5: Cloudflare, OpenRouter
    kb.row(
        InlineKeyboardButton(
            text="☁️ Cloudflare", callback_data=OnboardingCB.provider("cloudflare")
        ),
        InlineKeyboardButton(
            text="🔗 OpenRouter", callback_data=OnboardingCB.provider("openrouter")
        ),
    )
    # Row 6: STT providers (transcription)
    # TTS row removed — requires additional setup (API key + model), not available in this deployment.
    kb.row(
        InlineKeyboardButton(
            text="🎙️ STT (транскрипция)", callback_data=OnboardingCB.category("stt")
        ),
    )
    # Row 8: Custom provider
    kb.row(
        InlineKeyboardButton(
            text="➕ Свой провайдер", callback_data=OnboardingCB.custom("start")
        ),
    )
    # Row 9: Skip
    kb.row(
        InlineKeyboardButton(
            text="⏭️ Пропустить", callback_data=OnboardingCB.SKIP_LLM_KEY
        ),
    )
    await bot.send_message(chat_id, text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith(OnboardingCB.provider("")))
async def cb_onboarding_pick_provider(call: CallbackQuery, state: FSMContext) -> None:
    """Пользователь выбрал провайдера — запрашиваем ключ."""
    provider = call.data.split(":", 2)[2]
    await call.answer()

    # Сохраняем выбранного провайдера в стейт
    await state.update_data(onboarding_provider=provider)
    await state.set_state(OnboardingStates.waiting_llm_key)

    await call.message.answer(
        f"🔑 <b>{provider_display_name(provider)}</b>\n\n"
        "Пришли API-ключ.\n\n"
        "/cancel — назад к выбору."
    )


# ─── MiMo region step (onboarding) ──────────────────────────────────


async def _send_mimo_region_step(chat_id: int, bot) -> None:
    """Отправляет сообщение выбора региона MiMo."""
    kb = InlineKeyboardBuilder()
    for region_key, _region_url in MIMO_REGIONS.items():
        label = {"eu": "🇪🇺 EU", "us": "🇺🇸 US", "asia": "🌏 Asia"}.get(
            region_key, region_key.upper()
        )
        kb.button(text=label, callback_data=OnboardingCB.mimo_region(region_key))
    kb.button(
        text="⏭ Пропустить (Asia)", callback_data=OnboardingCB.mimo_region("skip")
    )
    kb.adjust(2)
    await bot.send_message(
        chat_id,
        "🌍 <b>Выбери регион MiMo API:</b>\n\n"
        "MiMo имеет региональные endpoint'ы. Выбери ближайший к тебе регион "
        "для минимальной задержки.\n\n"
        "• 🇪🇺 EU — Европа\n"
        "• 🇺🇸 US — США\n"
        "• 🌏 Asia — Азия (по умолчанию)\n\n"
        "/cancel — отмена.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith(OnboardingCB.mimo_region("")))
async def cb_onboarding_mimo_region(call: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает выбор региона MiMo в онбординге."""
    region_raw = call.data.split(":", 2)[2]
    await call.answer()

    if region_raw == "skip":
        endpoint = MIMO_REGIONS["asia"]
        region_label = "Asia (по умолчанию)"
    else:
        endpoint = MIMO_REGIONS.get(region_raw, MIMO_REGIONS["asia"])
        region_label = {"eu": "EU", "us": "US", "asia": "Asia"}.get(
            region_raw, region_raw
        )

    data = await state.get_data()
    mimo_key = data.get("onboarding_mimo_key", "")
    tg_id = call.from_user.id

    if mimo_key:
        parts = [k.strip() for k in mimo_key.split(",") if k.strip()]
        async with get_session() as session:
            owner = await get_or_create_user(session, tg_id)
            # Сохраняем в ApiKey (старое хранилище)
            await upsert_api_key(session, owner, "mimo", mimo_key)
            # Сохраняем в LlmKeySlot с endpoint (новое хранилище)
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
                # upsert_api_key мог создать слот без endpoint — обновляем
                if not slot.endpoint:
                    slot.endpoint = endpoint
            await session.flush()

    await state.set_state(OnboardingStates.waiting_llm_key)
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить ещё ключ", callback_data=OnboardingCB.GOBACK)
    kb.button(text="✅ Закончить", callback_data=OnboardingCB.DONE_KEYS)
    kb.adjust(2)
    if call.message:
        await call.message.edit_text(
            f"✅ Ключ <b>MiMo (Xiaomi)</b> сохранён! Регион: {region_label}.\n\n"
            "Хочешь добавить ещё ключей или провайдеров?",
            reply_markup=kb.as_markup(),
        )


@router.message(OnboardingStates.waiting_llm_key)
async def step_onboarding_llm_key_v2(message: Message, state: FSMContext) -> None:
    """Обрабатывает введённый API-ключ."""
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пустой ключ. Пришли API-ключ или /cancel.")
        return
    if raw in ("/cancel", "/back", "/menu"):
        await state.clear()
        await message.answer("❌ Ввод ключа отменён.")
        return

    data = await state.get_data()
    provider = data.get("onboarding_provider", "openai")
    tg_id = message.from_user.id

    validated, error_hint = await _validate_key_v2(provider, raw)
    if not validated:
        hint = (
            error_hint
            or f"Ключ {provider} не прошёл проверку. Убедись что ключ правильный."
        )
        await message.answer(f"❌ {hint}\n/cancel — отмена.")
        return

    try:
        await message.delete()
    except Exception:
        logger.debug("Non-critical error", exc_info=True)

    # MiMo: спросить регион перед сохранением в БД
    if provider == "mimo":
        await state.update_data(onboarding_mimo_key=raw)
        await _send_mimo_region_step(message.chat.id, message.bot)
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, tg_id)
        await upsert_api_key(session, owner, provider, raw)

    await state.set_state(OnboardingStates.waiting_llm_key)  # остаёмся в этом стейте
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить ещё ключ", callback_data=OnboardingCB.GOBACK)
    kb.button(text="✅ Закончить", callback_data=OnboardingCB.DONE_KEYS)
    kb.adjust(2)
    await message.answer(
        f"✅ Ключ <b>{provider_display_name(provider)}</b> сохранён и проверен!\n\n"
        "Хочешь добавить ещё ключей или провайдеров?",
        reply_markup=kb.as_markup(),
    )


@router.message(OnboardingStates.waiting_provider_choice, OwnerOnly())
async def step_onboarding_provider_choice_text(
    message: Message, state: FSMContext
) -> None:
    await message.answer("☝️ Нажми на кнопку провайдера выше, чтобы выбрать.")


@router.callback_query(F.data == OnboardingCB.GOBACK)
async def cb_onboarding_more_keys(call: CallbackQuery, state: FSMContext) -> None:
    """Пользователь хочет добавить ещё ключей — возвращаем к выбору провайдера."""
    await call.answer()
    await state.set_state(OnboardingStates.waiting_provider_choice)
    await _send_llm_key_step(call.message.chat.id, call.bot)
    await call.message.delete()


@router.callback_query(F.data == OnboardingCB.DONE_KEYS)
async def cb_onboarding_done_keys(call: CallbackQuery, state: FSMContext) -> None:
    """Пользователь закончил с ключами — переход к timezone."""
    await call.answer()
    await state.set_state(OnboardingStates.waiting_timezone)
    await call.message.delete()
    await _send_timezone_step(call.message.chat.id, call.bot)


# ─── Step 2b: TTS provider category ──────────────────────────────────


@router.callback_query(F.data == OnboardingCB.category("tts"))
async def cb_onboarding_tts_category(call: CallbackQuery) -> None:
    """Показывает TTS провайдеров."""
    await call.answer()
    text = (
        "🔊 <b>TTS провайдеры (озвучка)</b>\n\n"
        "Синтез речи — бот сможет озвучивать ответы голосом.\n"
        "Выбери провайдера:"
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🎵 OpenAI TTS", callback_data=OnboardingCB.tts("openai-tts")
        ),
        InlineKeyboardButton(
            text="📱 MiMo TTS", callback_data=OnboardingCB.tts("mimo-tts")
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="🌪️ Mistral TTS", callback_data=OnboardingCB.tts("mistral-tts")
        ),
    )
    kb.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=OnboardingCB.BACK),
    )
    await call.message.edit_text(text, reply_markup=kb.as_markup())


async def _send_stt_key_step(chat_id: int, bot: Bot) -> None:
    """Отправляет шаг выбора STT провайдера."""
    text = "🎙 <b>Шаг 2.2: Речевая транскрипция (STT)</b>\n\n"
    text += "Преобразование голоса в текст для лучшего понимания.\n\n"
    text += "Доступные провайдеры:"
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🎯 Whisper (OpenAI)", callback_data=OnboardingCB.stt("openai")
        ),
        InlineKeyboardButton(
            text="💎 Deepgram", callback_data=OnboardingCB.stt("deepgram")
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="🤖 Gemini STT", callback_data=OnboardingCB.stt("gemini")
        ),
        InlineKeyboardButton(
            text="🎤 AssemblyAI", callback_data=OnboardingCB.stt("assemblyai")
        ),
    )
    kb.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=OnboardingCB.BACK),
    )
    await bot.send_message(chat_id, text, reply_markup=kb.as_markup())


@router.callback_query(F.data == OnboardingCB.category("stt"))
async def cb_onboarding_stt_category(call: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает выбор категории STT."""
    await call.answer()
    await state.set_state(OnboardingStates.waiting_stt_provider)
    await _send_stt_key_step(call.message.chat.id, call.bot)


@router.callback_query(F.data.startswith(OnboardingCB.stt("")))
async def cb_onboarding_stt_provider(call: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает выбор конкретного STT провайдера."""
    provider = call.data.split(":")[-1]  # openai, deepgram, gemini, assemblyai
    await state.update_data(stt_provider=provider)
    await call.answer()
    await state.set_state(OnboardingStates.waiting_stt_key)

    provider_names = {
        "openai": "Whisper (OpenAI)",
        "deepgram": "Deepgram",
        "gemini": "Gemini STT",
        "assemblyai": "AssemblyAI",
    }
    provider_name = provider_names.get(provider, provider)

    text = f"🔑 <b>Введи API-ключ для {provider_name}</b>\n\n"
    text += "Ключ будет сохранён в зашифрованном виде.\n"
    text += "Напиши /cancel для отмены."

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="⬅️ Назад", callback_data=OnboardingCB.category("stt")
        ),
    )
    await call.message.edit_text(text, reply_markup=kb.as_markup())


@router.message(OnboardingStates.waiting_stt_provider)
async def step_onboarding_stt_provider_text(message: Message) -> None:
    """Text input when STT provider button expected."""
    await message.answer(
        "☝️ Нажми на кнопку STT-провайдера выше, чтобы выбрать. /cancel — отмена."
    )


@router.callback_query(F.data == OnboardingCB.BACK)
async def cb_onboarding_back(call: CallbackQuery, state: FSMContext) -> None:
    """Возврат назад в onboarding."""
    await call.answer()
    current_state = await state.get_state()

    # Определяем, куда возвращаться
    if current_state == OnboardingStates.waiting_stt_provider.state:
        await _send_llm_key_step(call.message.chat.id, call.bot)
    elif current_state == OnboardingStates.waiting_stt_key.state:
        await _send_stt_key_step(call.message.chat.id, call.bot)
        await state.set_state(OnboardingStates.waiting_stt_provider)
    else:
        await _send_llm_key_step(call.message.chat.id, call.bot)


# Обработчик для сохранения STT ключа
async def handle_stt_key_input(message: Message, state: FSMContext) -> None:
    """Сохраняет STT ключ с category='stt'."""
    if message.text and message.text.startswith("/"):
        if message.text == "/cancel":
            await state.set_state(OnboardingStates.waiting_stt_provider)
            await _send_stt_key_step(message.chat.id, message.bot)
            await message.reply("❌ Ввод ключа отменён.")
            return

    key = message.text.strip() if message.text else ""
    if not key:
        await message.reply(
            "❌ Ключ не может быть пустым. Попробуй ещё раз или /cancel"
        )
        return

    data = await state.get_data()
    provider = data.get("stt_provider", "openai")

    try:
        async with get_session() as session:
            user = await get_or_create_user(session, message.from_user.id)
            _slot, is_new = await add_key_slot(
                session,
                user,
                provider,
                key,
                purpose="main",
                category="stt",
            )
            await session.commit()

        provider_names = {
            "openai": "Whisper (OpenAI)",
            "deepgram": "Deepgram",
            "gemini": "Gemini STT",
            "assemblyai": "AssemblyAI",
        }
        provider_name = provider_names.get(provider, provider)

        if is_new:
            text = f"✅ <b>Ключ для {provider_name} успешно сохранён!</b>\n\n"
        else:
            text = "ℹ️ <b>Этот ключ уже был добавлен ранее.</b>\n\n"

        text += "Теперь можешь продолжить настройку или завершить.\n\n"
        text += "Что дальше?"

        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(
                text="➕ Ещё STT ключ", callback_data=OnboardingCB.category("stt")
            ),
            InlineKeyboardButton(
                text="⚙️ Настройки", callback_data=OnboardingCB.GO_SETTINGS
            ),
        )
        kb.row(
            InlineKeyboardButton(
                text="✅ Завершить", callback_data=OnboardingCB.FINISH
            ),
        )
        await message.reply(text, reply_markup=kb.as_markup())
        await state.clear()

    except Exception as e:
        logger.warning("save_stt_key failed: %s", e)
        await message.reply("❌ Ошибка сохранения ключа. Попробуй ещё раз или /cancel")


# Регистрируем обработчик STT ключа
@router.message(OnboardingStates.waiting_stt_key)
async def on_stt_key_message(message: Message, state: FSMContext) -> None:
    """Роутер для обработки STT ключа."""
    await handle_stt_key_input(message, state)


@router.callback_query(F.data.startswith(OnboardingCB.tts("")))
async def cb_onboarding_tts_pick(call: CallbackQuery) -> None:
    """TTS провайдеры — требуется дополнительная настройка (API-ключ + модель)."""
    # TTS requires additional setup (API key + model). Not available in this deployment.
    await call.answer(
        "🔊 TTS требует API-ключ и модель. Недоступно в этом деплойменте.",
        show_alert=True,
    )


@router.callback_query(F.data == OnboardingCB.back_extra("provider_select"))
async def cb_onboarding_back_to_providers(call: CallbackQuery) -> None:
    """Возвращается к выбору провайдера."""
    await call.answer()
    await _send_llm_key_step(call.message.chat.id, call.bot)
    await call.message.delete()


@router.callback_query(F.data == OnboardingCB.SKIP_LLM_KEY)
async def cb_onboarding_skip_llm(call: CallbackQuery, state: FSMContext) -> None:
    """Пропускает добавление LLM-ключа."""
    await call.answer()
    await state.set_state(OnboardingStates.waiting_timezone)
    await call.message.edit_text("⏭️ <b>LLM-ключ пропущен.</b>")
    await _send_timezone_step(call.message.chat.id, call.bot)


@router.callback_query(F.data == OnboardingCB.custom("start"))
async def cb_onboarding_custom_start(call: CallbackQuery, state: FSMContext) -> None:
    """Начинает добавление кастомного провайдера."""
    await call.answer()
    await state.set_state(CustomProviderStates.waiting_provider_name)
    await call.message.answer(
        "➕ <b>Свой провайдер</b>\n\n"
        'Шаг 1/4: Пришли название провайдера (например, "My Local LLM").\n'
        "/cancel — назад к выбору."
    )


# ─── Step 2c: Custom provider FSM flow ────────────────────────────────


@router.message(CustomProviderStates.waiting_provider_name)
async def step_custom_provider_name(message: Message, state: FSMContext) -> None:
    """Сохраняет название кастомного провайдера."""
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. /cancel — отмена.")
        return
    await state.update_data(custom_provider_name=name)
    await state.set_state(CustomProviderStates.waiting_endpoint)
    await message.answer(
        f"✅ Название: <b>{name}</b>\n\n"
        "Шаг 2/4: Пришли endpoint URL (например, https://api.openai.com/v1).\n"
        "/cancel — отмена."
    )


@router.message(CustomProviderStates.waiting_endpoint)
async def step_custom_provider_endpoint(message: Message, state: FSMContext) -> None:
    """Сохраняет endpoint URL."""
    url = (message.text or "").strip()
    if not url:
        await message.answer("URL не может быть пустым. /cancel — отмена.")
        return
    await state.update_data(custom_provider_endpoint=url)
    await state.set_state(CustomProviderStates.waiting_key)
    await message.answer(
        f"✅ Endpoint: <code>{url}</code>\n\n"
        "Шаг 3/4: Пришли API-ключ.\n"
        "/cancel — отмена."
    )


@router.message(CustomProviderStates.waiting_key)
async def step_custom_provider_key(message: Message, state: FSMContext) -> None:
    """Сохраняет ключ."""
    key = (message.text or "").strip()
    if not key:
        await message.answer("Ключ не может быть пустым. /cancel — отмена.")
        return
    try:
        await message.delete()
    except Exception:
        logger.debug("Non-critical error", exc_info=True)
    await state.update_data(custom_provider_key=key)
    await state.set_state(CustomProviderStates.waiting_model)
    await message.answer(
        "✅ Ключ сохранён.\n\n"
        "Шаг 4/4: Пришли название модели через запятую "
        "(лёгкая, тяжёлая, vision).\n"
        "Например: <code>llama3:8b,llama3:70b,llava:13b</code>\n\n"
        "Если модель одна — просто пришли её название.\n"
        "/cancel — отмена."
    )


@router.message(CustomProviderStates.waiting_model)
async def step_custom_provider_model(message: Message, state: FSMContext) -> None:
    """Сохраняет модели и завершает кастомного провайдера."""
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Укажи хотя бы одну модель. /cancel — отмена.")
        return

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    models = {
        "chat_light": parts[0] if len(parts) >= 1 else "default",
        "chat_heavy": parts[1] if len(parts) >= 2 else parts[0] if parts else "default",
        "vision": parts[2] if len(parts) >= 3 else None,
    }

    data = await state.get_data()
    provider_name = data.get("custom_provider_name", "custom")
    endpoint = data.get("custom_provider_endpoint", "")
    key = data.get("custom_provider_key", "")
    tg_id = message.from_user.id

    async with get_session() as session:
        owner = await get_or_create_user(session, tg_id)
        # Сохраняем каждый слот с метаданными
        for purpose, model in models.items():
            if model:
                await add_key_slot(
                    session,
                    owner,
                    "custom",
                    key,
                    purpose=purpose,
                    model=model,
                    endpoint=endpoint,
                    label=provider_name,
                    category="llm",
                )

    await state.set_state(OnboardingStates.waiting_llm_key)
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить ещё ключ", callback_data=OnboardingCB.GOBACK)
    kb.button(text="✅ Закончить", callback_data=OnboardingCB.DONE_KEYS)
    kb.adjust(2)
    await message.answer(
        f"✅ Кастомный провайдер <b>{provider_name}</b> добавлен!\n"
        f"Модели: {', '.join(v for v in models.values() if v)}\n\n"
        "Добавить ещё ключ или перейти дальше?",
        reply_markup=kb.as_markup(),
    )


async def _validate_key_v2(provider: str, key: str) -> tuple[bool, str | None]:
    """Валидирует ключ через провайдера. Возвращает (valid, error_hint)."""
    try:
        _provider_cls = {
            "openai": OpenAIProvider,
            "gemini": GeminiProvider,
            "mistral": MistralProvider,
            "cloudflare": CloudflareProvider,
            "openrouter": OpenRouterProvider,
            "anthropic": AnthropicProvider,
            "deepseek": DeepSeekProvider,
            "grok": GrokProvider,
            "mimo": MiMoProvider,
            "groq": GroqProvider,
        }.get(provider)

        if _provider_cls is None:
            return (False, f"Неизвестный провайдер: {provider}")

        return (await _provider_cls(key).validate_key(), None)
    except Exception as e:
        err_str = safe_str(e).lower()
        if any(
            w in err_str
            for w in ("timeout", "connect", "resolve", "network", "refused", "reset")
        ):
            return (False, "Сетевая ошибка. Проверь подключение и попробуй снова.")
        logger.exception("Key validation failed for %s", provider)
        return (False, "Не удалось проверить ключ. Попробуй позже.")


# ─── Step 3: Timezone ─────────────────────────────────────────────────


async def _send_timezone_step(chat_id: int, bot) -> None:
    """Отправляет сообщение шага «Часовой пояс»."""
    # Строим клавиатуру с популярными TZ
    rows = []
    for tz_name in TZ_PRESETS:
        label = _tz_button_label(tz_name)
        rows.append(
            [
                InlineKeyboardButton(
                    text=label, callback_data=OnboardingCB.timezone(tz_name)
                )
            ]
        )

    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    await bot.send_message(
        chat_id,
        "🕐 <b>Шаг 3/4 — часовой пояс</b>\n\n"
        "Выбери свой город или введи вручную (например, Europe/Moscow)",
        reply_markup=kb,
    )


def _tz_button_label(tz_name: str) -> str:
    """Короткая метка кнопки TZ."""
    try:
        short = tz_short(tz_name)
        return short
    except Exception:
        return tz_name


@router.callback_query(F.data.startswith(OnboardingCB.timezone("")))
async def cb_onboarding_tz(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь выбрал часовой пояс из списка."""
    tz_value = callback.data[len("onboarding:tz:") :]
    if not is_valid_tz(tz_value):
        await callback.answer("Неизвестный часовой пояс", show_alert=True)
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        owner.settings.timezone = tz_value

    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(callback.from_user.id)

    await state.set_state(OnboardingStates.waiting_sync_choice)
    if callback.message is None:
        await callback.answer("Сообщение недоступно.")
        return
    await callback.answer(f"✅ Часовой пояс: {tz_short(tz_value)}")
    await _send_sync_step(callback.message.chat.id, callback.bot)

    # Убираем клавиатуру
    try:
        await callback.message.edit_text(
            f"✅ Часовой пояс: <b>{tz_short(tz_value)}</b>"
        )
    except Exception:
        logger.debug("Non-critical error", exc_info=True)


@router.message(OnboardingStates.waiting_timezone)
async def step_onboarding_timezone(message: Message, state: FSMContext) -> None:
    """Пользователь ввёл часовой пояс текстом."""
    tz_value = (message.text or "").strip()
    if not is_valid_tz(tz_value):
        await message.answer(
            "Не нашёл такой TZ. Используй IANA-формат, например "
            "<code>Europe/Moscow</code>.\n"
            "Или выбери из списка выше.\n"
            "/cancel — отмена."
        )
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.timezone = tz_value

    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(message.from_user.id)

    await state.set_state(OnboardingStates.waiting_sync_choice)
    await message.answer(f"✅ Часовой пояс: <b>{tz_short(tz_value)}</b>")
    await _send_sync_step(message.chat.id, message.bot)


# ─── Step 4: Sync choice ──────────────────────────────────────────────


async def _send_sync_step(chat_id: int, bot) -> None:
    """Отправляет сообщение шага «Синхронизация чатов»."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Все личные чаты",
                    callback_data=OnboardingCB.sync("all"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📂 Выбрать папки",
                    callback_data=OnboardingCB.sync("folders"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏭ Пропустить",
                    callback_data=OnboardingCB.sync("skip"),
                ),
            ],
        ]
    )

    await bot.send_message(
        chat_id,
        "📱 <b>Шаг 4/4 — синхронизация контактов</b>\n\n"
        "Я прочитаю твои диалоги и запомню важное. Это займёт 2-5 минут.",
        reply_markup=kb,
    )


@router.callback_query(F.data == OnboardingCB.sync("all"))
async def cb_onboarding_sync_all(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь выбрал синхронизацию всех личных чатов."""
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        owner.settings.monitor_only_selected_folders = False

    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(callback.from_user.id)

    # Запускаем синхронизацию
    from src.userbot import get_active_telethon_client
    from src.userbot.dialogs import sync_dialogs

    client = get_active_telethon_client(callback.from_user.id)
    if client is None:
        await callback.answer(
            "❌ Telegram-сессия не активна. Сначала /login.", show_alert=True
        )
        return

    await callback.answer("📱 Начинаю синхронизацию...")

    try:
        await sync_dialogs(client, owner)
        status = "✅ Список чатов обновлён!"
    except Exception as exc:
        logger.warning("sync_dialogs during onboarding failed: %s", exc)
        status = "⚠️ Синхронизация не удалась. Попробуй позже"

    await state.clear()
    await _finish_onboarding(
        callback.message.chat.id,
        callback.bot,
        tg_id=callback.from_user.id,
        extra=status,
    )

    if callback.message:
        try:
            await callback.message.edit_text("📱 Синхронизация запущена ✅")
        except Exception:
            logger.debug("Non-critical error", exc_info=True)


@router.callback_query(F.data == OnboardingCB.sync("folders"))
async def cb_onboarding_sync_folders(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Пользователь выбрал синхронизацию по папкам."""
    # Запрашиваем имена папок
    await state.set_state(OnboardingStates.waiting_sync_choice)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "📂 Напиши названия папок через запятую, которые нужно отслеживать.\n\n"
            "Например: <code>Работа, Семья, Друзья</code>\n\n"
            "Или нажми /cancel чтобы пропустить."
        )


@router.message(OnboardingStates.waiting_sync_choice)
async def step_onboarding_sync_folders_text(
    message: Message, state: FSMContext
) -> None:
    """Пользователь ввёл названия папок для синхронизации."""
    folders_text = (message.text or "").strip()
    if not folders_text:
        await message.answer(
            "Пустой список. Напиши названия папок через запятую или /cancel."
        )
        return

    folder_names = [f.strip() for f in folders_text.split(",") if f.strip()]
    if not folder_names:
        await message.answer(
            "Нужно хотя бы одно название папки. Попробуй ещё раз или /cancel."
        )
        return

    import json

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.monitored_folders = json.dumps(folder_names)
        owner.settings.monitor_only_selected_folders = True

    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(message.from_user.id)

    # Запускаем синхронизацию
    from src.userbot import get_active_telethon_client
    from src.userbot.dialogs import sync_dialogs

    client = get_active_telethon_client(message.from_user.id)
    if client is None:
        status = "❌ Telegram-сессия не активна. Сначала /login."
    else:
        try:
            await sync_dialogs(client, owner)
            status = "✅ Чаты из выбранных папок синхронизированы!"
        except Exception as exc:
            logger.warning("sync_dialogs during onboarding (folders) failed: %s", exc)
            status = "⚠️ Синхронизация не удалась. Попробуй позже"

    await state.clear()
    await _finish_onboarding(
        message.chat.id,
        message.bot,
        tg_id=message.from_user.id,
        extra=f"📂 Папки: {', '.join(folder_names)}\n{status}",
    )


@router.callback_query(F.data == OnboardingCB.sync("skip"))
async def cb_onboarding_sync_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь пропустил синхронизацию."""
    await state.clear()
    if callback.message is None:
        await callback.answer("Сообщение недоступно.")
        return
    await callback.answer("Ок, можно настроить позже в /settings → Синхронизация")

    try:
        await callback.message.edit_text("⏭ Синхронизация пропущена")
    except Exception:
        logger.debug("Non-critical error", exc_info=True)
        return
    await _finish_onboarding(
        callback.message.chat.id, callback.bot, tg_id=callback.from_user.id
    )


# ─── STT post-save callbacks ────────────────────────────────────────


@router.callback_query(F.data == OnboardingCB.FINISH)
async def cb_onboarding_finish(call: CallbackQuery) -> None:
    """Завершает онбординг с кнопки «✅ Завершить» после сохранения STT-ключа."""
    await call.answer()
    if call.message is None:
        return
    await _finish_onboarding(call.message.chat.id, call.bot, tg_id=call.from_user.id)


@router.callback_query(F.data == OnboardingCB.GO_SETTINGS)
async def cb_onboarding_go_settings(call: CallbackQuery, state: FSMContext) -> None:
    """Открывает настройки с кнопки «⚙️ Настройки» после сохранения STT-ключа."""
    await call.answer()
    await state.clear()
    from src.bot.handlers.settings_menu import _render_menu

    text, kb = await _render_menu(call.from_user.id)
    if call.message:
        try:
            await call.message.edit_text(text, reply_markup=kb)
        except Exception:
            logger.debug("Non-critical error", exc_info=True)


# ─── Finish ────────────────────────────────────────────────────────────


async def _finish_onboarding(chat_id: int, bot, tg_id: int, extra: str = "") -> None:
    """Финальное сообщение с детальным саммари после завершения онбординга."""

    tone_labels = {
        "professional": "Деловой",
        "friendly": "Тёплый",
        "efficient": "Эффективный",
        "default": "Стандартный",
        "cynical": "Циничный",
        "warm": "Тёплый",
    }

    async with get_session() as session:
        owner = await get_or_create_user(session, tg_id)

        # Считаем контакты
        contact_count: int = (
            await session.scalar(
                select(func.count())
                .select_from(Contact)
                .where(Contact.user_id == owner.id)
            )
        ) or 0

        # Считаем активные факты в памяти
        fact_count: int = (
            await session.scalar(
                select(func.count())
                .select_from(Memory)
                .where(Memory.user_id == owner.id, Memory.is_active.is_(True))
            )
        ) or 0

        # Данные сессии
        session_label = "—"
        if owner.session:
            session_label = owner.session.account_label or owner.session.phone or "—"

        # LLM ключи
        providers = sorted(
            {k.provider for k in owner.key_slots if getattr(k, "enabled", True)}
        )
        key_names = ", ".join(
            provider_display_name(p, pretty_openrouter=True) for p in providers
        )
        if not key_names:
            key_names = "—"

        # Часовой пояс
        tz_name = owner.settings.timezone or "UTC"

        # Режим личности
        persona = await session.scalar(
            select(AdaptivePersona).where(AdaptivePersona.user_id == owner.id)
        )
        tone_key = persona.base_tone if persona else "default"
        tone_label = tone_labels.get(tone_key, tone_key)

    msg = (
        "<b>Итог настройки</b>\n"
        f"• Сессия: {session_label}\n"
        f"• Контакты: {contact_count}\n"
        f"• Факты в памяти: {fact_count}\n"
        f"• LLM-ключи: {len(providers)} ({key_names})\n"
        f"• Часовой пояс: {tz_name}\n"
        f"• Тон: {tone_label}\n\n"
        "🎉 <b>Я полностью настроен и готов к работе!</b>\n\n"
        "Что я теперь умею:\n"
        "🧠 Помню факты о тебе и контактах\n"
        "💬 Авто-отвечаю в ЛС пока ты занят\n"
        "📋 Веду список дел и напоминаю\n"
        "📰 Собираю дайджест новостей\n"
        "🔍 Ищу по истории переписок\n"
        "🌤️ Погода, крипта, whois, таймеры\n\n"
        "Просто напиши мне — я пойму.\n"
        "Подробнее: /help"
    )
    if extra:
        msg = extra + "\n\n" + msg

    await bot.send_message(chat_id, msg)


# ─── advance_onboarding_after_login ────────────────────────────────────


async def advance_onboarding_after_login(message: Message, state: FSMContext) -> bool:
    """Вызывается из login.py после успешного входа.

    Если пользователь ещё не прошёл онбординг — переводит на следующий шаг
    и возвращает True. Если онбординг не нужен — возвращает False.
    """
    tg_id = message.from_user.id
    async with get_session() as session:
        owner = await get_or_create_user(session, tg_id)
        has_session = owner.session is not None
        has_llm_key = len(owner.key_slots) > 0
        has_tz = owner.settings.timezone not in (None, "", "UTC", "Etc/UTC")

    # Если после логина пользователь уже полностью готов — не вмешиваемся
    if has_session and has_llm_key and has_tz:
        return False

    # Переходим к выбору провайдера
    await state.set_state(OnboardingStates.waiting_provider_choice)
    await message.answer(
        "✅ Готово! <b>Шаг 2/4 — API-ключ</b>\n\nТеперь нужен ключ для доступа к LLM. Выбери провайдера:"
    )
    await _send_llm_key_step(message.chat.id, message.bot)
    return True
