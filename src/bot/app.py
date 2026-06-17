import asyncio
import logging
from time import time

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Message

from src.bot.handlers import (
    analyze_cmd,
    approve_cmd,
    ask_cmd,
    avito_cmd,
    catchup_cmd,
    chat_cmd,
    contact_cmd,
    cron_cmd,
    digest_cmd,
    docs_cmd,
    draft_actions,
    explain_cmd,
    gates_cmd,
    health_cmd,
    humanize_cmd,
    inbox_cmd,
    inline_query,
    install_cmd,
    keys_cmd,
    mode_cmd,
    models_cmd,
    monitor_cmd,
    free_text_legacy,
    free_text_memory,
    free_text_settings,
    greeting,
    login,
    memory_admin_cmds,
    memory_cmd,
    memory_correction,
    memory_inbox,
    news_cmd,
    news_topics,
    profile_cmd,
    research_cb,
    pubmed_cmd,
    search,
    send,
    sessions_cmd,
    settings as settings_handlers,
    skills_cmd,
    start,
    stats_cmd,
    style_cmd,
    threads_cmd,
    timeline_cmd,
    today_cmd,
    todos,
    trajectory_cmd,
    wiki_cmd,
)
from src.bot.handlers.free_text import confirm_router
from src.config import settings
from src.core.infra.notifier import notifier
from src.core.security.pairing import pairing
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)


def _retry_wrapper(send_fn):
    """Wrap bot.send_message with exponential backoff on 429 / network errors.

    This covers ALL callers (message.answer(), notifier, safe_send, etc.)
    with zero changes to handler code.
    """

    async def wrapper(chat_id, text, **kwargs):
        max_retries = 3
        base_delay = 2.0
        for attempt in range(max_retries):
            try:
                return await send_fn(chat_id, text, **kwargs)
            except TelegramRetryAfter as e:
                delay = max(e.retry_after, base_delay * (2**attempt))
                logger.warning(
                    "Telegram 429: waiting %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)
            except TelegramNetworkError:
                if attempt == max_retries - 1:
                    logger.exception("Telegram network error, max retries reached")
                    raise
                delay = base_delay * (2**attempt)
                logger.warning(
                    "Telegram network error, retrying in %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)
        raise RuntimeError(f"send_message failed after {max_retries} retries")

    return wrapper


async def access_guard_middleware(
    handler, event: Message | types.CallbackQuery, data: dict
) -> None:
    """Owner-only access guard + DM pairing for unknown contacts.

    Paired contacts receive a ``_paired_user`` flag in the context data so
    that downstream ``OwnerOnly`` filter lets them through. Unknown contacts
    get a pairing code and cannot reach any handlers until approved.
    """
    user = getattr(event, "from_user", None)
    if user is None:
        return await handler(event, data)

    tg_id = user.id
    if tg_id == settings.owner_telegram_id:
        return await handler(event, data)

    if await pairing.is_allowed(tg_id):
        data["_paired_user"] = True
        return await handler(event, data)

    if await pairing.is_pending(tg_id):
        answer = getattr(event, "answer", None)
        if answer:
            await answer("⏳ Ваш запрос на доступ ожидает подтверждения владельца.")
        return

    try:
        code = await pairing.start_pairing(tg_id)
    except Exception:
        logger.exception("pairing.start_pairing failed for tg_id=%d", tg_id)
        answer = getattr(event, "answer", None)
        if answer:
            await answer("⚠️ Произошла ошибка. Попробуйте позже.")
        return
    answer = getattr(event, "answer", None)
    if answer:
        await answer(
            f"🔐 Для доступа передай владельцу код:\n<code>{code}</code>\n"
            f"Он выполнит: <code>/approve {tg_id} {code}</code>"
        )
    return


def _setup_bot_and_dispatcher(
    userbot_manager: UserbotManager,
) -> tuple[Bot, Dispatcher]:
    """Собирает Bot + Dispatcher со всеми роутерами и middleware.

    Вынесено в отдельную функцию чтобы переиспользовать
    и для polling, и для webhook.
    """
    session = AiohttpSession(proxy=settings.proxy_url) if settings.proxy_url else None

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )
    notifier.attach(bot)

    # Patch bot.send_message so ALL outbound messages (message.answer, etc.)
    # automatically get retry with exponential backoff.
    bot.send_message = _retry_wrapper(bot.send_message)

    dp = Dispatcher(storage=MemoryStorage())

    dp["userbot_manager"] = userbot_manager

    # ─── Access guard: owner + DM pairing ───
    dp.message.outer_middleware()(access_guard_middleware)
    dp.callback_query.outer_middleware()(access_guard_middleware)

    # ─── Онбординг-гард: фазовая блокировка команд ───
    # Кэш фазы онбординга — замыкание middleware, живёт пока жив Dispatcher
    _phase_cache: dict[int, tuple[int, float]] = {}
    _phase_cache_ttl = 60.0

    @dp.message.outer_middleware()
    async def onboarding_guard_middleware(
        handler, message: Message, data: dict
    ) -> None:
        """Перенаправляет не-онбордингнутых пользователей на нужный шаг.

        Фазы:
          1 (нет сессии)     — только /start, /login, /cancel
          2 (нет LLM-ключа)  — плюс /keys, /settings
          3 (нет часового)   — всё разрешено, но подсказка /sync после ответа
          4 (готов)          — без ограничений
        """
        if not message.from_user:
            return  # channel posts, no user context

        tg_id = message.from_user.id
        if tg_id != settings.owner_telegram_id:
            return await handler(message, data)

        # Всегда пропускаем голосовые/аудио — они обрабатываются отдельно
        if getattr(message, "voice", None) or getattr(message, "audio", None):
            return await handler(message, data)

        # Всегда пропускаем команды онбординга
        text = message.text or ""
        if text.startswith(("/start", "/login", "/cancel")):
            return await handler(message, data)

        # Если пользователь в любом FSM — не вмешиваемся
        state: FSMContext | None = data.get("state")
        if state is not None:
            current = await state.get_state()
            if current is not None:
                return await handler(message, data)

        from src.bot.filters import get_onboarding_phase

        # ── Cache onboarding phase (TTL 60s) — avoids DB query per message ──
        now = time()

        cached = _phase_cache.get(tg_id)
        if cached and now - cached[1] < _phase_cache_ttl:
            phase = cached[0]
        else:
            phase = await get_onboarding_phase(tg_id)
            _phase_cache[tg_id] = (phase, now)

        # Фаза 4 — всё настроено, пропускаем
        if phase == 4:
            return await handler(message, data)

        # Фаза 1 — нет сессии: только /start, /login, /cancel (уже прущены выше)
        if phase == 1:
            try:
                await message.answer("Сначала сделай /login")
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
            return

        # Фаза 2 — нет LLM-ключа: разрешаем /keys и /settings
        if phase == 2:
            if text.startswith(("/keys", "/settings")):
                return await handler(message, data)
            try:
                await message.answer("Теперь добавь API-ключ для LLM. Жми /keys add.")
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
            return

        # Фаза 3 — нет часового пояса / синхронизации:
        # разрешаем всё, но после ответа показываем подсказку /sync
        await handler(message, data)
        try:
            await message.answer("💡 Хочешь чтобы я запомнил важное? Сделай /sync.")
        except Exception:
            logger.debug("Non-critical error", exc_info=True)
        return

    # Inline-режим — самый первый, чтобы ловить @botname до команд
    dp.include_router(inline_query.router)
    dp.include_router(approve_cmd.router)
    dp.include_router(ask_cmd.router)
    dp.include_router(research_cb.router)
    dp.include_router(gates_cmd.router)
    dp.include_router(health_cmd.router)
    dp.include_router(stats_cmd.router)
    dp.include_router(docs_cmd.router)
    dp.include_router(inbox_cmd.router)
    dp.include_router(install_cmd.router)
    dp.include_router(start.router)
    dp.include_router(greeting.router)
    dp.include_router(analyze_cmd.router)
    dp.include_router(contact_cmd.router)
    dp.include_router(profile_cmd.router)
    dp.include_router(login.router)
    dp.include_router(settings_handlers.router)
    dp.include_router(chat_cmd.router)
    dp.include_router(catchup_cmd.router)
    dp.include_router(send.router)
    dp.include_router(search.router)
    dp.include_router(pubmed_cmd.router)
    dp.include_router(todos.router)
    dp.include_router(digest_cmd.router)
    dp.include_router(style_cmd.router)
    dp.include_router(models_cmd.router)
    dp.include_router(keys_cmd.router)
    dp.include_router(memory_inbox.router)
    dp.include_router(memory_admin_cmds.router)
    dp.include_router(memory_cmd.router)
    dp.include_router(memory_correction.router)  # FSM consumer for /memory --correct
    dp.include_router(news_cmd.router)
    dp.include_router(draft_actions.router)
    dp.include_router(news_topics.router)
    dp.include_router(threads_cmd.router)
    dp.include_router(timeline_cmd.router)
    dp.include_router(sessions_cmd.router)
    dp.include_router(explain_cmd.router)
    dp.include_router(humanize_cmd.router)
    dp.include_router(mode_cmd.router)
    dp.include_router(today_cmd.router)
    dp.include_router(skills_cmd.router)
    dp.include_router(cron_cmd.router)
    dp.include_router(trajectory_cmd.router)
    dp.include_router(wiki_cmd.router)
    dp.include_router(avito_cmd.router)
    dp.include_router(monitor_cmd.router)
    dp.include_router(free_text_memory.router)
    dp.include_router(free_text_settings.router)
    dp.include_router(confirm_router)
    from src.bot.handlers.nudge import nudge_router

    dp.include_router(nudge_router)
    # ВАЖНО: free_text — самым последним, чтобы команды и FSM перехватили текст раньше
    dp.include_router(free_text_legacy.router)

    return bot, dp


async def run_bot(userbot_manager: UserbotManager) -> None:
    """Запуск бота в режиме long-polling."""
    bot, dp = _setup_bot_and_dispatcher(userbot_manager)

    me = await bot.get_me()
    logger.info("Control bot started as @%s (polling)", me.username)

    from src.bot.command_registry import CommandRegistry, register_all_commands

    registry = CommandRegistry()
    register_all_commands(registry)
    await bot.set_my_commands(registry.as_telegram_commands())
    logger.info(
        "Bot commands menu updated: %d commands",
        len(registry.as_telegram_commands()),
    )

    try:
        # close_bot_session=False — сессией управляем явно в finally
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        await bot.session.close()


async def run_bot_webhook(userbot_manager: UserbotManager) -> None:
    """Запуск бота в режиме webhook (задайте WEBHOOK_URL в .env).

    Использует aiohttp + feed_webhook_update — нативный подход aiogram 3.17+.
    """
    from aiohttp import web

    bot, dp = _setup_bot_and_dispatcher(userbot_manager)

    webhook_url = (settings.webhook_url or "").rstrip("/")
    webhook_path = settings.webhook_path or "/webhook"
    webhook_port = settings.webhook_port

    if not webhook_url:
        logger.error("webhook_url is empty — falling back to polling")
        return await run_bot(userbot_manager)

    # Set Telegram webhook
    full_webhook_url = f"{webhook_url}{webhook_path}"
    if settings.webhook_secret_token:
        await bot.set_webhook(
            full_webhook_url, secret_token=settings.webhook_secret_token
        )
    else:
        await bot.set_webhook(full_webhook_url)
    logger.info(
        "Webhook set to %s (port %d, path %s, secret_token=%s)",
        full_webhook_url,
        webhook_port,
        webhook_path,
        "configured" if settings.webhook_secret_token else "not set",
    )

    me = await bot.get_me()
    logger.info("Control bot started as @%s (webhook)", me.username)

    from src.bot.command_registry import CommandRegistry, register_all_commands

    registry = CommandRegistry()
    register_all_commands(registry)
    await bot.set_my_commands(registry.as_telegram_commands())

    # Build minimal aiohttp app for webhook ingestion
    aiohttp_app = web.Application()

    async def _handle_update(request: web.Request) -> web.Response:
        # Validate secret token if configured (prevents fake updates from non-Telegram sources)
        if settings.webhook_secret_token:
            header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if header_token != settings.webhook_secret_token:
                return web.Response(status=403, text="Forbidden")
        try:
            data = await request.json()
            update = types.Update(**data)
            await dp.feed_webhook_update(bot, update)
        except Exception:
            logger.debug("Webhook update processing failed", exc_info=True)
        return web.Response(status=200)

    aiohttp_app.router.add_post(webhook_path, _handle_update)

    runner = web.AppRunner(aiohttp_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", webhook_port)
    await site.start()

    logger.info(
        "Webhook server listening on 127.0.0.1:%d%s",
        webhook_port,
        webhook_path,
    )

    try:
        # Keep running until cancelled (handled by main() shutdown)
        stop_event = asyncio.Event()
        await stop_event.wait()
    finally:
        await runner.cleanup()
        await bot.session.close()
