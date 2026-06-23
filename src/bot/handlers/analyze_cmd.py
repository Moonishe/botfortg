"""Команда /analyze — полный анализ переписок."""

import asyncio
import logging
from collections.abc import Sequence

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.core.infra.text_sanitizer import sanitize_html
from src.db.session import get_session
from src.db.repo import get_or_create_user, list_contacts
from src.llm.base import TaskType
from src.llm.router import build_provider
from src.core.infra.full_analyzer import (
    run_full_analysis,
    format_analysis_report,
    AnalysisProgress,
)

logger = logging.getLogger(__name__)
router = Router(name="analyze_cmd")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())

# R3: Debounce — prevent double-click starting two parallel /analyze runs
_analyze_running = asyncio.Lock()


async def _resolve_contact_names(
    contacts, names: Sequence[str], userbot_manager, telegram_id: int
) -> tuple[list[int], list[str]]:
    """Пытается найти контакты по имени — точное совпадение → fuzzy через contact_resolver."""
    resolved = []
    unresolved = []
    for name in names:
        nl = name.strip().lower()
        found = False
        for c in contacts:
            cn = (c.display_name or "").lower()
            if nl == cn or (len(nl) > 2 and nl in cn):
                resolved.append(c.peer_id)
                found = True
                break
        if not found:
            try:
                from src.bot.contact_resolver import resolve_contact_fast

                client = (
                    userbot_manager.get_client(telegram_id) if userbot_manager else None
                )
                if client:
                    async with get_session() as _s:
                        owner = await get_or_create_user(_s, telegram_id)
                    candidates = await resolve_contact_fast(client, owner, name)
                    if candidates:
                        resolved.append(candidates[0].peer_id)
                        found = True
            except Exception:
                logger.debug("contact resolve failed", exc_info=True)
        if not found:
            unresolved.append(name)
    return resolved, unresolved


@router.message(Command("analyze"))
async def cmd_analyze(message: Message, state=None, userbot_manager=None):
    """Запуск полного анализа — показывает выбор режима."""
    args = (message.text or "").strip().split()
    folder_filter = args[1:] if len(args) > 1 else []

    # Сохраняем аргументы в state для использования в callback
    if state:
        await state.update_data(analyze_folders=folder_filter)

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="⚡ Только новые (инкремент)",
            callback_data="analyze:incr:text",
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="📝 Все сообщения (текст)",
            callback_data="analyze:full:text",
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="📷 Все сообщения (текст + фото)",
            callback_data="analyze:full:photos",
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="⚡ Последние 500 (быстро)",
            callback_data="analyze:quick:text",
        ),
    )
    if folder_filter:
        folders_str = sanitize_html(", ".join(folder_filter))
        hint = f"📂 Папки: {folders_str}\n\n"
    else:
        hint = "📂 Все контакты\n\n"

    await message.answer(
        f"🧠 <b>Анализ переписок</b>\n\n"
        f"{hint}"
        "Выбери режим анализа:\n\n"
        "⚡ <b>Только новые</b> — инкремент, пропускает контакты без новых сообщений\n"
        "📝 <b>Все сообщения (текст)</b> — полная переписка, только текст\n"
        "📷 <b>Все + фото</b> — полная переписка, фото описываются через vision\n"
        "⚡ <b>Последние 500</b> — быстро, последние 500 сообщений на контакт",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("analyze:"))
async def cb_analyze_run(callback: CallbackQuery, state=None, userbot_manager=None):
    """Запускает анализ в выбранном режиме."""
    await callback.answer()

    # R3: Debounce — prevent double-click starting two parallel runs
    if _analyze_running.locked():
        await callback.answer("⏳ Анализ уже идёт, подожди...", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        return
    scope = parts[1]  # "full", "quick", or "incr"
    photo_mode = parts[2]  # "text" or "photos"
    include_photos = photo_mode == "photos"
    message_limit = 0 if scope in ("full", "incr") else 500  # 0 = all
    incremental = scope == "incr"

    # Restore folder filter from state
    folder_filter = []
    if state:
        data = await state.get_data()
        folder_filter = data.get("analyze_folders", [])

    status_msg = await callback.message.answer("🧠 Запускаю анализ...")
    await callback.message.edit_text("📱 Анализ запущен ✅")

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        provider = await build_provider(session, owner, task_type=TaskType.SUMMARIZE)

        import json

        if owner.settings is None:
            monitored = []
        else:
            monitored = (
                json.loads(owner.settings.monitored_folders)
                if owner.settings.monitored_folders
                else []
            )

        contact_ids_arg = None
        folders_to_analyze = folder_filter if folder_filter else monitored
        if not folders_to_analyze:
            folders_to_analyze = None

        if not provider:
            await status_msg.edit_text(
                "❌ Не удалось создать LLM провайдер. Проверь API ключи."
            )
            return

    # Если есть аргументы — пробуем разрешить как имена контактов
    if folder_filter:
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            contacts = await list_contacts(
                session, owner, kinds=("user",), include_bots=False
            )
        resolved_ids, unresolved = await _resolve_contact_names(
            contacts, folder_filter, userbot_manager, callback.from_user.id
        )
        if resolved_ids:
            contact_ids_arg = resolved_ids
            folders_to_analyze = None
        if unresolved and not resolved_ids:
            pass
        elif unresolved and resolved_ids:
            await status_msg.edit_text(
                "❌ Не удалось найти контакты: "
                + sanitize_html(", ".join(unresolved))
                + "."
            )
            return

    # Callback для обновления прогресса
    async def update_progress(progress: AnalysisProgress):
        try:
            if progress.phase == "scan":
                await status_msg.edit_text(f"🔍 {progress.message}")
            elif progress.phase == "processing":
                bar_filled = min(progress.current, progress.total)
                bar = "▓" * bar_filled + "░" * max(0, progress.total - bar_filled)
                photo_tag = " 📷" if include_photos else ""
                await status_msg.edit_text(
                    f"🔄 [{bar}] {progress.current}/{progress.total}\n"
                    f"📂 {progress.contact_name}{photo_tag}"
                )
            elif progress.phase == "done":
                await status_msg.edit_text("✅ Анализ завершён, формирую отчёт...")
        except Exception:
            logger.debug("progress update failed", exc_info=True)

    try:
        client = (
            userbot_manager.get_client(callback.from_user.id)
            if userbot_manager
            else None
        )
        async with _analyze_running:
            result = await run_full_analysis(
                owner_id=callback.from_user.id,
                provider=provider,
                client=client,
                message_limit=message_limit,
                folder_names=folders_to_analyze,
                contact_ids=contact_ids_arg,
                progress_callback=update_progress,
                include_photos=include_photos,
                incremental=incremental,
            )

        report = format_analysis_report(result)

        # ── Proactive insights: actionable suggestions instead of just numbers ──
        try:
            from src.bot.handlers.nl_router import generate_insights

            insights = await generate_insights(callback.from_user.id)
            if insights:
                report += "\n\n<b>💡 Инсайты:</b>\n" + "\n".join(
                    f"  {i}" for i in insights
                )
        except Exception:
            logger.debug("Insight generation failed", exc_info=True)

        await status_msg.edit_text(report)

    except Exception as e:
        logger.warning("full_analysis failed: %s", e)
        await status_msg.edit_text("❌ Ошибка анализа. Попробуй позже")
