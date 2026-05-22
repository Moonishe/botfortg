from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.bot.filters import OwnerOnly
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.core.infra.timeutil import tz_short


router = Router(name="start")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


def _pretty_provider(name: str | None) -> str:
    """Человеческое имя провайдера для отображения."""
    names = {
        "openrouter": "OpenRouter (DeepSeek V4)",
        "openai": "OpenAI",
        "gemini": "Gemini",
        "mistral": "Mistral",
        "cloudflare": "Cloudflare",
    }
    return names.get(name or "", "—")


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


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        has_session = owner.session is not None
        llm = _pretty_provider(owner.settings.llm_provider)
        tz = tz_short(owner.settings.timezone) if owner.settings.timezone else "UTC"

        # Проверяем, новый ли пользователь (нет persona или 0 взаимодействий)
        from src.db.models._learning import AdaptivePersona
        from sqlalchemy import select

        stmt = select(AdaptivePersona).where(AdaptivePersona.user_id == owner.id)
        result = await session.execute(stmt)
        persona = result.scalar_one_or_none()

    is_new = (persona is None) or (persona.total_interactions == 0)

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

    kb = InlineKeyboardMarkup(
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
                    text="🎭 Личность", callback_data="set:sec:personality"
                ),
            ],
        ]
    )
    await message.answer(header + WELCOME + onboarding_text, reply_markup=kb)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        auth_status = "✅" if owner.session else "❌"
        llm = _pretty_provider(owner.settings.llm_provider)
    header = (
        f"📖 <b>Помощь по командам</b>\n"
        f"{'Ты авторизован' if owner.session else 'Не авторизован'} {auth_status} · "
        f"LLM: {llm}\n\n"
    )
    await message.answer(header + WELCOME)


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
    # Перенаправляем: удаляем клавиатуру и показываем текст с командой
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
            pass
