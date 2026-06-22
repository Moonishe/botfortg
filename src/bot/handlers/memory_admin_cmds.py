"""Memory admin utility commands — extracted from memory_cmd.py (Stage 5 refactor).

Commands: /llm_status, /remember, /habits, /insights, /forget,
          /archetypes, /distill, /instructions, /tag, /conflicts, /warnings,
          /clusters, /persona, /memory
Callbacks: memory:clear_negative, memory:stats, pattern:*, mem:neighbors:*,
           conflict:resolve:*, summary_save:*
"""

import logging
from datetime import datetime, UTC

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.bot.callback_utils import safe_callback_edit
from src.bot.filters import OwnerOnly
from src.core.contacts.contact_resolver import resolve
from src.core.infra.text_sanitizer import sanitize_html
from src.core.infra.timeutil import ensure_utc as _ensure_utc
from src.core.memory.memory_fuel import (
    format_depleted_contacts,
    format_fuel_line,
    get_fuel_stats,
)
from src.core.memory.memory_neighbors import format_neighbors, get_neighbors
from src.core.memory.memory_service import (
    bulk_delete_memory_service,
    save_memory_single,
)
from src.core.memory.stats import get_memory_stats
from src.db.repo import (
    get_contact,
    get_or_create_user,
    get_persona,
    list_key_slots,
    list_memories,
    search_memories,
    update_persona,
)
from src.db.session import get_session
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)
router = Router(name="memory_admin_cmds")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


@router.message(Command("llm_status"))
async def cmd_llm_status(message: Message) -> None:
    """Показать статус LLM: семафоры, слоты, использование."""
    from src.llm.router import _PURPOSE_SEMAPHORES

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        slots = await list_key_slots(session, owner)

    lines = ["<b>📊 LLM Status</b>", ""]
    total_used = sum(s.usage_count or 0 for s in slots)
    total_fail = sum(s.failure_count or 0 for s in slots)
    lines.append(f"Всего вызовов: {total_used} | фейлов: {total_fail}")
    lines.append("")

    # _PURPOSE_SEMAPHORES is None until ensure_locks_initialized() runs at
    # startup — guard against that or .items() raises AttributeError on None.
    semaphores = _PURPOSE_SEMAPHORES or {}
    for purpose, sem in semaphores.items():
        # sem._value / sem._bound_value are the only way to introspect an
        # asyncio.Semaphore without acquiring it. Both have been stable
        # since 3.10; fall back gracefully if a future CPython rename drops
        # them. ``_value`` is the count of *available* slots, ``_bound_value``
        # is the original limit.
        active = getattr(sem, "_value", "?")
        limit = getattr(sem, "_bound_value", "?")
        lines.append(f"🔹 {purpose}: {active}/{limit} слотов свободно")
    lines.append("")
    for s in slots[:10]:
        cooldown_active = (c := _ensure_utc(s.cooldown_until)) and c > datetime.now(UTC)
        status = "❌" if not s.enabled else "⏳" if cooldown_active else "✅"
        lines.append(
            f"{status} <b>{s.provider}</b> / {s.purpose} — {s.usage_count}× ({s.failure_count}× фейлов)"
        )

    await message.answer("\n".join(lines))


@router.callback_query(F.data == "memory:clear_negative")
async def cb_memory_clear_negative(callback: CallbackQuery) -> None:
    """Удалить все негативные факты."""
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        items = await list_memories(session, owner)
        negative_ids = [m.id for m in items if m.sentiment == "negative"]
        removed = await bulk_delete_memory_service(session, owner, negative_ids)
    if callback.message:
        await callback.message.edit_text(f"🧹 Удалено {removed} негативных фактов.")  # type: ignore[union-attr]
    await callback.answer(f"Удалено {removed}")


# Removed: dead code, /health handled by health_cmd.py


@router.callback_query(F.data == "memory:stats")
async def cb_memory_stats(callback: CallbackQuery) -> None:
    """Показать детальную статистику памяти."""
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        stats = await get_memory_stats(session, owner)

    lines = [
        "📊 <b>Статистика памяти</b>",
        "",
        f"🧠 Всего фактов: {stats['total']}",
        "",
        "<b>По тональности:</b>",
    ]
    for sentiment, count in stats["by_sentiment"].items():
        emoji = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(
            sentiment, "⚪"
        )
        lines.append(f"  {emoji} {sentiment}: {count}")
    lines.extend(
        [
            "",
            "<b>По источникам:</b>",
        ]
    )
    for source, count in stats["by_source"].items():
        lines.append(f"  📄 {source}: {count}")
    lines.extend(
        [
            "",
            f"🎯 Высокая уверенность (≥0.8): {stats['high_confidence']}",
            f"👤 Связано с контактами: {stats['with_contact']}",
        ]
    )

    # Индикатор топлива памяти
    fuel = await get_fuel_stats(callback.from_user.id)
    lines.append("")
    lines.append(format_fuel_line(fuel))
    depleted_text = format_depleted_contacts(fuel)
    if depleted_text:
        lines.append(depleted_text)

    if callback.message:
        await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.message(Command("remember"))
async def cmd_remember(
    message: Message, command: CommandObject, userbot_manager: UserbotManager | None
) -> None:
    """Вручную сохранить факт. /remember Настя злится из-за дедлайна"""
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Использование: <code>/remember [контакт] факт</code>\nПример: <code>/remember Настя злится</code>"
        )
        return

    # пробуем отделить имя контакта от факта
    contact_name = None
    fact = args
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
    client = (
        userbot_manager.get_client(message.from_user.id) if userbot_manager else None
    )
    if client is not None:
        candidates = await resolve(client, owner, args)
        if candidates and candidates[0].score >= 70:
            contact_name = candidates[0].label()
            # пытаемся отделить: берём первое слово как имя
            words = args.split(None, 1)
            if len(words) > 1:
                fact = words[1]

    contact_id = None
    if contact_name and client is not None:
        candidates = await resolve(client, owner, contact_name)
        if candidates:
            contact_id = candidates[0].peer_id

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        await save_memory_single(
            session, owner, fact=fact, contact_id=contact_id, source="user"
        )

    await message.answer(sanitize_html(f"🧠 Запомнил: <i>{fact}</i>"))


@router.message(Command("habits"))
async def cmd_habits(message: Message) -> None:
    """Показать обнаруженные привычки на основе повторяющихся фактов."""
    from src.core.scheduling.habit_tracker import find_habit_candidates, format_habits

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        memories = await list_memories(session, owner)
        active = [m for m in memories if m.is_active and m.created_at]
    habits = find_habit_candidates(active)
    text = format_habits(habits)
    await message.answer(text)


@router.message(Command("insights"))
async def cmd_insights(message: Message) -> None:
    from src.core.memory.memory_patterns import detect_patterns, format_insights

    insights = await detect_patterns(message.from_user.id)
    text, keyboards = format_insights(insights)
    # Если инсайтов нет — шлём один текст
    if not insights:
        await message.answer(text)
        return
    # Если есть — каждый инсайт отдельным сообщением с клавиатурой
    for ins, kb in zip(insights[:5], keyboards, strict=False):
        detail = (
            f"<b>{sanitize_html(ins['title'])}</b>\n"
            f"{sanitize_html(ins['detail'])}\n"
            f"💡 {sanitize_html(ins['action'])}"
        )
        await message.answer(detail, reply_markup=kb)


@router.message(Command("forget"))
async def cmd_forget(
    message: Message, command: CommandObject, userbot_manager: UserbotManager
) -> None:
    """Удалить факты по подстроке. /forget злится"""
    args = (command.args or "").strip()
    if not args:
        await message.answer("Использование: <code>/forget часть текста</code>")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        found = await search_memories(session, owner, args)

    if not found:
        await message.answer("Ничего не нашёл.")
        return

    async with get_session() as session:
        owner = await session.merge(owner)
        await bulk_delete_memory_service(session, owner, [m.id for m in found])

    names = ", ".join(
        f"«{m.fact[:50]}…»" if len(m.fact) > 50 else f"«{m.fact}»" for m in found
    )
    await message.answer(sanitize_html(f"🗑 Забыл: {names}"))


@router.message(Command("archetypes"))
async def cmd_archetypes(message: Message) -> None:
    """Показать архетипы всех контактов."""
    from src.core.contacts.contact_archetypes import (
        classify_all_contacts,
        format_archetype_stats,
    )

    await message.answer("🏷 Анализирую контакты...")
    stats = await classify_all_contacts(message.from_user.id)
    text = format_archetype_stats(stats)
    await message.answer(text)


@router.message(Command("distill"))
async def cmd_distill(message: Message, userbot_manager: UserbotManager) -> None:
    """Запустить дистилляцию фактов (10+ → 1 summary)."""
    from src.core.memory.knowledge_distiller import run_distillation

    args = (message.text or "").split()
    contact_name = args[1] if len(args) > 1 else None
    contact_id = None
    if contact_name:
        client = (
            userbot_manager.get_client(message.from_user.id)
            if userbot_manager
            else None
        )
        if client is not None:
            async with get_session() as session:
                owner = await get_or_create_user(session, message.from_user.id)
            candidates = await resolve(client, owner, contact_name)
            if candidates:
                contact_id = candidates[0].peer_id

    await message.answer("🧠 Запускаю дистилляцию...")
    result = await run_distillation(message.from_user.id, contact_id)
    if result["success"]:
        await message.answer(
            f"✅ <b>Дистилляция завершена:</b>\n"
            f"Сжато {result['deactivated']} фактов →\n"
            f"<i>«{sanitize_html(result['fact'][:200])}»</i>"
        )
    else:
        await message.answer("❌ Недостаточно фактов для дистилляции (нужно 10+).")


@router.callback_query(F.data.startswith("pattern:"))
async def cb_pattern_action(callback: CallbackQuery) -> None:
    """Обрабатывает нажатия на inline-кнопки паттернов."""
    data = callback.data.split(":")
    action = data[1]  # remind, dismiss, history, write
    contact_id = int(data[2]) if len(data) > 2 else 0

    if action == "dismiss":
        existing = (
            callback.message.text if isinstance(callback.message, Message) else None
        )
        await safe_callback_edit(
            callback,
            f"{existing}\n\n🔕 Ок, не сейчас." if existing else "🔕 Ок, не сейчас.",
        )
        await callback.answer()
        return

    if action == "remind":
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            contact = await get_contact(session, owner, contact_id)
            name = contact.display_name if contact else str(contact_id)
            # Сохраняем факт в память
            await save_memory_single(
                session,
                owner,
                fact=f"Пользователь хочет напоминание о созвоне с {name}",
                source="user",
                sentiment="neutral",
            )
        await safe_callback_edit(
            callback,
            sanitize_html(
                f"📅 Напоминание для <b>{name}</b>\n"
                f"Напиши: <code>/remind за час до созвона с {name}</code>"
            ),
        )
        await callback.answer(f"Напоминание для {sanitize_html(name)}")
        return

    if action == "history":
        await callback.answer(
            f"История контакта {contact_id} — открой /chat {contact_id} или /memory"
        )
        return

    if action == "write":
        await callback.answer("Напиши: /send контакт текст")
        return

    await callback.answer()


@router.message(Command("instructions"))
async def cmd_instructions(message: Message) -> None:
    from src.core.intelligence.adaptive_instructions import get_active_rules

    async with get_session() as session:
        await get_or_create_user(session, message.from_user.id)
    rules = await get_active_rules(message.from_user.id)
    if not rules:
        await message.answer(
            "У тебя нет активных правил. Скажи что-то вроде «отвечай короче» или «не используй смайлики»."
        )
        return
    lines = ["<b>📋 Активные правила:</b>", ""]
    for i, r in enumerate(rules, 1):
        lines.append(f"{i}. {r}")
    await message.answer("\n".join(lines))


@router.message(Command("tag"))
async def cmd_tag(message: Message) -> None:
    """Проставить теги всем нетэгированным фактам."""
    from src.core.memory.memory_tagger import tag_all_untagged

    await message.answer("🏷 Тегирую факты...")
    count = await tag_all_untagged(message.from_user.id)
    if count > 0:
        await message.answer(f"✅ Протегировано {count} фактов.")
    else:
        await message.answer("✅ Все факты уже протегированы, или нет активных фактов.")


@router.callback_query(F.data.startswith("mem:neighbors:"))
async def cb_mem_neighbors(callback: CallbackQuery) -> None:
    """Показать семантических соседей для факта памяти."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    try:
        mid = int(parts[2])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    neighbors = await get_neighbors(callback.from_user.id, mid)
    text = format_neighbors(neighbors)
    if text:
        if callback.message:
            await callback.message.answer(text)
        await callback.answer()
    else:
        await callback.answer("Соседей не найдено")


@router.message(Command("conflicts"))
async def cmd_conflicts(message: Message) -> None:
    """Показать и разрешить конфликты в памяти."""
    from src.core.actions.conflict_resolver import find_conflicts, format_conflicts

    conflicts = await find_conflicts(message.from_user.id)
    text = format_conflicts(conflicts)
    await message.answer(text)


@router.message(Command("warnings"))
async def cmd_warnings(message: Message) -> None:
    """Показать активные предупреждения о риске конфликтов."""
    from src.core.actions.conflict_predictor import (
        detect_silence_triggers,
        format_conflict_warnings,
    )

    triggers = await detect_silence_triggers(message.from_user.id)
    text = format_conflict_warnings(triggers) or "✅ Нет рисков конфликтов."
    await message.answer(text)


@router.callback_query(F.data.startswith("conflict:resolve:"))
async def cb_conflict_resolve(callback: CallbackQuery) -> None:
    """Обработать разрешение конфликта памяти."""
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    try:
        positive_id = int(parts[2])
        negative_id = int(parts[3])
    except ValueError:
        await callback.answer("Неверные данные.", show_alert=True)
        return
    resolution = parts[4]
    from src.core.actions.conflict_resolver import resolve_conflict

    success = await resolve_conflict(
        callback.from_user.id, positive_id, negative_id, resolution
    )
    if success:
        existing = (
            callback.message.text if isinstance(callback.message, Message) else None
        )
        await safe_callback_edit(
            callback,
            f"{existing}\n\n✅ Конфликт разрешён."
            if existing
            else "✅ Конфликт разрешён.",
        )
        await callback.answer()
    else:
        await callback.answer("Ошибка при разрешении конфликта")


@router.message(Command("clusters"))
async def cmd_clusters(message: Message) -> None:
    """Показать кластеры памяти."""
    from src.db.repo import list_clusters_for_contact

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        clusters = await list_clusters_for_contact(session, owner)

    if not clusters:
        await message.answer(
            "Нет кластеров памяти. Они создадутся автоматически ночью."
        )
        return

    lines = ["<b>🧩 Кластеры памяти:</b>", ""]
    for cluster, fact_count in clusters[:8]:
        lines.append(f"📦 <b>{cluster.topic}</b> — {fact_count} фактов")
        if cluster.summary:
            lines.append(f"   <i>{cluster.summary[:80]}</i>")
    await message.answer("\n".join(lines))


@router.message(Command("memory"))
async def cmd_memory_summary(
    message: Message,
    command: CommandObject,
    userbot_manager: UserbotManager,
) -> None:
    """Ручной пересказ чата: /memory summary <имя чата>"""
    args = (command.args or "").strip().split()
    if not args or args[0] != "summary":
        await message.answer(
            "Использование: <code>/memory summary &lt;имя чата&gt;</code>\n"
            "Пример: <code>/memory summary Маша</code>"
        )
        return

    chat_query = " ".join(args[1:]) if len(args) > 1 else ""
    if not chat_query:
        await message.answer(
            "Укажи имя чата или контакта.\nПример: <code>/memory summary Маша</code>"
        )
        return

    # Разрешаем контакт по имени
    client = (
        userbot_manager.get_client(message.from_user.id) if userbot_manager else None
    )
    contact_name = chat_query
    chat_id: int | None = None

    if client is not None:
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
        candidates = await resolve(client, owner, chat_query)
        if candidates and candidates[0].score >= 55:
            contact_name = candidates[0].label()
            chat_id = candidates[0].peer_id

    if chat_id is None:
        await message.answer(
            f"Не удалось найти контакт «{sanitize_html(chat_query)}». "
            f"Проверь имя и попробуй снова."
        )
        return

    await message.answer(
        f"🧠 Делаю пересказ чата <b>{sanitize_html(contact_name)}</b>…"
    )

    from src.core.memory.chat_summarizer import (
        generate_chat_summary,
        save_summary_checkpoint,
    )

    user_id = message.from_user.id
    summary = await generate_chat_summary(chat_id, user_id)

    # Сохраняем чекпоинт после успешной генерации
    if not summary.startswith("❌") and not summary.startswith("📭"):
        await save_summary_checkpoint(chat_id, user_id, 0)

    # Кнопка «Сохранить в память»
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💾 Сохранить в память",
                    callback_data=f"summary_save:{chat_id}",
                )
            ]
        ]
    )

    await message.answer(
        f"📊 <b>Пересказ: {sanitize_html(contact_name)}</b>\n\n{summary}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("summary_save:"))
async def cb_summary_save(callback: CallbackQuery) -> None:
    """Сохранить текст пересказа как факт памяти."""
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer("Неверные данные.", show_alert=True)
        return
    # Извлекаем текст пересказа из сообщения (после заголовка)
    msg_text: str | None = None
    if callback.message is not None:
        msg_text = getattr(callback.message, "text", None)
    if msg_text is None:
        await callback.answer("Не удалось извлечь текст.")
        return

    # Текст сообщения: "📊 <b>Пересказ: Имя</b>\n\n...summary..."
    # Отделяем заголовок от тела
    parts = msg_text.split("\n\n", 1)
    summary_text = parts[1] if len(parts) > 1 else msg_text
    # Убираем HTML-теги для сохранения в память как чистый текст
    import re

    clean_text = re.sub(r"<[^>]+>", "", summary_text).strip()

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        await save_memory_single(
            session,
            owner,
            fact=f"[Пересказ чата #{chat_id}]: {clean_text[:500]}",
            contact_id=chat_id,
            source="summary",
            sentiment="neutral",
        )

    await callback.answer("✅ Сохранено в память!")
    # Обновляем кнопку — убираем её
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
        except Exception:
            logger.debug(
                "Non-critical error", exc_info=True
            )  # кнопка уже убрана или сообщение недоступно


@router.message(Command("persona"))
async def cmd_persona(message: Message) -> None:
    """Показать/сбросить адаптивный профиль личности."""

    args = (message.text or "").split()

    # /persona reset — сброс
    if len(args) > 1 and args[1] == "reset":
        async with get_session() as session:
            owner_db = await get_or_create_user(session, message.from_user.id)
            p = await get_persona(session, owner_db)
            await update_persona(
                session,
                p,
                brevity="normal",
                formality="friendly",
                emoji_usage="normal",
                initiative="reactive",
                preferred_format="text",
                work_mode="normal",
                forbidden_patterns=None,
                max_response_len=500,
            )
        await message.answer("✅ Персона сброшена к стандартным настройкам.")
        return

    # /persona — показать
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        p = await get_persona(session, owner)

    brevity_labels = {
        "short": "📝 Коротко",
        "normal": "📝 Обычно",
        "detailed": "📝 Подробно",
    }
    formality_labels = {
        "formal": "👔 Формально",
        "friendly": "🤝 Дружелюбно",
        "casual": "😎 Панибратски",
    }
    emoji_labels = {
        "none": "🚫 Без эмодзи",
        "minimal": "😊 Минимум",
        "normal": "😊 Обычно",
        "rich": "🎉 Много",
    }
    initiative_labels = {
        "reactive": "🔇 По запросу",
        "proactive": "📢 Инициативный",
        "balanced": "⚖️ Умеренно",
    }
    work_labels = {
        "normal": "💼 Обычный",
        "focus": "🎯 Фокус",
        "relax": "🏖 Отдых",
    }
    format_labels = {
        "text": "📄 Текст",
        "bullets": "📋 Список",
        "numbered": "🔢 Нумерация",
    }

    lines = ["<b>🧑‍🎤 Твой стиль общения:</b>", ""]
    lines.append(brevity_labels.get(p.brevity or "", p.brevity or "?"))
    lines.append(formality_labels.get(p.formality or "", p.formality or "?"))
    lines.append(emoji_labels.get(p.emoji_usage or "", p.emoji_usage or "?"))
    lines.append(initiative_labels.get(p.initiative or "", p.initiative or "?"))
    lines.append(format_labels.get(p.preferred_format or "", p.preferred_format or "?"))
    lines.append(work_labels.get(p.work_mode or "", p.work_mode or "?"))
    lines.append(f"📏 Макс. длина: {p.max_response_len} символов")
    lines.append(f"🔄 Коррекций: {p.total_corrections}")
    lines.append("")
    lines.append(
        "<i>Скажи «отвечай короче», «будь формальнее», «без смайликов» — я подстроюсь.\n"
        "/persona reset — сбросить всё.</i>"
    )
    await message.answer("\n".join(lines))
