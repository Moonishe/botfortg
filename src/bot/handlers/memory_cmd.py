import json
import logging
import time

from sqlalchemy import select

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.bot.filters import OwnerOnly
from src.bot.states import MemoryCorrectionStates
from src.config import settings
from src.core.contacts.contact_resolver import resolve
from src.core.infra.text_sanitizer import sanitize_html
from src.core.memory.memory_fuel import (
    format_depleted_contacts,
    format_fuel_line,
    get_fuel_stats,
)
from src.db.models import Commitment, Memory, MemoryLink
from src.db.repo import (
    get_commitment_by_source_memory,
    get_graph_stats,
    get_memory_stats,
    get_or_create_user,
    list_memories,
    list_memory_candidates,
)
from src.db.session import get_session
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)
router = Router(name="memory_cmd")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())

# /memory --correct использует FSM-состояние MemoryCorrectionStates.waiting_new_text
# (см. src.bot.handlers.memory_correction). Этот модуль только:
#   • ставит состояние (в _cmd_memory_correct)
#   • сбрасывает состояние (в cb_memreval cancel/reject/permanent)
# Сам потребитель (handle_pending_correction) живёт в memory_correction.router.

# ─── Dispatcher ───────────────────────────────────────────────────────


@router.message(Command("memory"))
async def cmd_memory(
    message: Message, state: FSMContext, userbot_manager: UserbotManager
) -> None:
    """Тонкий диспетчер: парсит режим и делегирует подфункциям.

    Режимы (сохранены один-в-один из исходной god-function):
      --inbox, --forget-sweep, --reval, --correct, --graph:export, --graph,
      --impact, --tag, --timeline, --story, (default = view)
    """
    raw = (message.text or "").replace("/memory", "").strip()

    if "--inbox" in raw:
        return await _cmd_memory_inbox(message)
    if "--forget-sweep" in raw:
        return await _cmd_memory_forget_sweep(message)
    if "--reval" in raw:
        return await _cmd_memory_reval(message)
    if "--correct" in raw:
        return await _cmd_memory_correct(message, raw, state)
    if "--graph:export" in raw:
        return await _cmd_memory_graph_export(message)
    if "--graph" in raw:
        return await _cmd_memory_graph_stats(message)
    if "--impact" in raw:
        name = raw.replace("--impact", "", 1).strip()
        return await _cmd_memory_impact(message, userbot_manager, name)
    if "--tag" in raw:
        parts = raw.split("--tag", 1)
        tag = parts[1].strip().split()[0] if len(parts) > 1 and parts[1].strip() else ""
        return await _cmd_memory_tag(message, tag)
    if "--timeline" in raw:
        name = raw.replace("--timeline", "", 1).strip()
        return await _cmd_memory_timeline(message, userbot_manager, name)
    if "--story" in raw:
        name = raw.replace("--story", "", 1).strip()
        return await _cmd_memory_story(message, userbot_manager, name)
    if "--history" in raw:
        memory_id_str = raw.replace("--history", "", 1).strip()
        return await _cmd_memory_history(message, memory_id_str)

    # default = view
    return await _cmd_memory_view(message, userbot_manager, raw)


# ─── Режим: --inbox ──────────────────────────────────────────────────


async def _cmd_memory_inbox(message: Message) -> None:
    """Показать входящие факты (Memory Inbox) с кнопками подтверждения."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        candidates = await list_memory_candidates(session, owner)
    if not candidates:
        await message.answer("📭 Входящих фактов на подтверждение нет.")
        return
    lines = ["📬 <b>Входящие факты (Memory Inbox):</b>", ""]
    for i, c in enumerate(candidates, 1):
        sent_emoji = {
            "positive": "🟢",
            "negative": "🔴",
            "neutral": "⚪",
        }.get(c.sentiment or "", "⚪")
        mem_type = f" ({c.memory_type})" if c.memory_type else ""
        lines.append(
            f"{i}. {sent_emoji} <i>{sanitize_html(c.fact)}</i>{mem_type}\n"
            f"   важность={c.importance}, затухание={c.decay_rate}, источник={c.source}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Запомнить",
                        callback_data=f"memb:confirm:{c.id}",
                    ),
                    InlineKeyboardButton(
                        text="✏️ Исправить",
                        callback_data=f"memb:edit:{c.id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="⏳ На неделю",
                        callback_data=f"memb:temporary:{c.id}",
                    ),
                    InlineKeyboardButton(
                        text="♾ Навсегда",
                        callback_data=f"memb:permanent:{c.id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Удалить",
                        callback_data=f"memb:discard:{c.id}",
                    ),
                ],
            ]
        )
        await message.answer(lines[-1], reply_markup=kb)


# ─── Режим: --forget-sweep ──────────────────────────────────────────


async def _cmd_memory_forget_sweep(message: Message) -> None:
    """Запустить auto-forget sweep — деактивировать низко-retention факты."""
    from src.core.memory.auto_forget import auto_forget_sweep

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        count = await auto_forget_sweep(session, owner.id)
        await session.commit()
    await message.answer(
        f"🧹 Auto-forget sweep: deactivated {count} low-retention facts."
    )


# ─── Режим: --reval ─────────────────────────────────────────────────


async def _cmd_memory_reval(message: Message) -> None:
    """Запустить LLM-переоценку памяти (Dreaming V3)."""
    from src.core.memory.dreaming_reval import reval_run, reval_summary_text

    await message.answer("🧠 Dreaming V3: запускаю LLM-переоценку…")
    summary = await reval_run(
        message.from_user.id,
        limit=getattr(settings, "dreaming_reval_max_per_run", 50),
    )
    text = reval_summary_text(summary)
    # Если были изменения — показываем кнопки для подтверждения
    if summary.past + summary.permanent + summary.invalid > 0:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📋 Подробнее",
                        callback_data="memreval:detail",
                    ),
                    InlineKeyboardButton(
                        text="↩️ Откатить все",
                        callback_data="memreval:rollback_all",
                    ),
                ]
            ]
        )
        await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text)


# ─── Режим: --correct <id> ───────────────────────────────────────────


async def _cmd_memory_correct(
    message: Message, raw_args: str, state: FSMContext
) -> None:
    """Поставить факт в очередь на ручное исправление (FSM).

    Пишет в FSMContext: MemoryCorrectionStates.waiting_new_text + memory_id +
    original_fact + set_at_ts. Потребитель — handle_pending_correction в
    memory_correction.router.
    """
    parts = raw_args.replace("--correct", "", 1).strip().split()
    if not parts or not parts[0].isdigit():
        await message.answer(
            "Использование: <code>/memory --correct &lt;id&gt;</code>\n"
            "Пример: <code>/memory --correct 1234</code>"
        )
        return
    memory_id = int(parts[0])
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        fact = await session.get(Memory, memory_id)
        if not fact or fact.user_id != owner.id:
            await message.answer(f"❌ Факт #{memory_id} не найден.")
            return
        original = fact.fact
        await session.commit()
    # Set FSM state — consumer is handle_pending_correction in
    # memory_correction router. Lazy TTL via set_at_ts in state data.
    await state.set_state(MemoryCorrectionStates.waiting_new_text)
    await state.update_data(
        memory_id=memory_id,
        original_fact=original,
        set_at_ts=time.monotonic(),
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="♾ Сделать постоянным",
                    callback_data=f"memreval:permanent:{memory_id}",
                ),
                InlineKeyboardButton(
                    text="🗑 Деактивировать",
                    callback_data=f"memreval:reject:{memory_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="memreval:cancel",
                ),
            ],
        ]
    )
    await message.answer(
        f"✏️ <b>Исправление факта #{memory_id}</b>\n\n"
        f"<i>{sanitize_html(original)}</i>\n\n"
        f"Напиши новый текст одним сообщением, или нажми кнопку:",
        reply_markup=kb,
    )


# ─── Режим: --graph:export ──────────────────────────────────────────


async def _cmd_memory_graph_export(message: Message) -> None:
    """Экспорт графа памяти (узлы + рёбра) в JSON."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        # Nodes: active memories (max 500)
        all_mems = await list_memories(session, owner, limit=500, is_active=True)
        nodes = [
            {
                "id": m.id,
                "fact": m.fact,
                "contact_id": m.contact_id,
                "memory_type": m.memory_type,
                "importance": m.importance,
                "sentiment": m.sentiment,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in all_mems
        ]
        # Edges: all links for user
        edges_result = await session.execute(
            select(
                MemoryLink.source_id,
                MemoryLink.target_id,
                MemoryLink.weight,
                MemoryLink.relation_type,
            ).where(MemoryLink.user_id == owner.id)
        )
        edges = [
            {
                "source": int(r.source_id),
                "target": int(r.target_id),
                "weight": float(r.weight),
                "relation_type": r.relation_type,
            }
            for r in edges_result.all()
        ]
    payload = {"nodes": nodes, "edges": edges}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    # Telegram message limit ~4000 chars for safety
    if len(text) > 3900:
        text = text[:3900] + "\n...]}"  # truncated
    await message.answer(f"<b>📊 Graph export:</b>\n<pre>{sanitize_html(text)}</pre>")


# ─── Режим: --graph ─────────────────────────────────────────────────


async def _cmd_memory_graph_stats(message: Message) -> None:
    """Показать статистику knowledge graph (узлы, рёбра, хабы)."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        stats = await get_graph_stats(session, owner.id)
    lines = [
        "📊 <b>Knowledge Graph Statistics</b>",
        "",
        f"🧠 <b>Nodes (active memories):</b> {stats['node_count']}",
        f"🔗 <b>Edges (links):</b> {stats['total_edges']}",
        f"📏 <b>Average degree:</b> {stats['avg_degree']}",
        f"🔗 <b>Connected components:</b> {stats['components']}",
        f"🕳 <b>Isolated nodes:</b> {stats['isolated_nodes']}",
        "",
        "<b>Edges by type:</b>",
    ]
    for rel_type, cnt in sorted(stats["edges_by_type"].items(), key=lambda x: -x[1]):
        lines.append(f"  • <b>{rel_type}</b>: {cnt}")
    if stats["top_hubs"]:
        lines.extend(["", "<b>Top-5 hub nodes:</b>"])
        for hub in stats["top_hubs"]:
            fact_snippet = sanitize_html(hub["fact"][:60])
            lines.append(
                f"  🔗 <b>ID {hub['memory_id']}</b> — degree {hub['degree']}: "
                f"«{fact_snippet}»"
            )
    await message.answer("\n".join(lines))


# ─── Режим: --impact <name> ──────────────────────────────────────────


async def _cmd_memory_impact(
    message: Message, userbot_manager: UserbotManager, contact_name: str
) -> None:
    """Показать impact контакта в графе памяти."""
    if not contact_name:
        await message.answer("Использование: /memory --impact @имя_контакта")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        client = (
            userbot_manager.get_client(message.from_user.id)
            if userbot_manager
            else None
        )
        if client is None:
            await message.answer("⚠️ Userbot не подключён.")
            return
        candidates = await resolve(client, owner, contact_name)
        if not candidates:
            await message.answer(f"Контакт «{contact_name}» не найден.")
            return
        from src.db.repos.memory_repo import contact_impact

        impact = await contact_impact(session, owner.id, candidates[0].peer_id)
    lines = [
        f"📊 <b>Impact: {sanitize_html(impact.contact_name)}</b>",
        "",
        f"📌 Фактов: {len(impact.direct_facts)}",
        f"👥 Связанных контактов: {len(impact.related_contacts)}",
        f"🕸 Всего узлов в графе: {impact.total_nodes}",
    ]
    if impact.topics:
        lines.append(f"🏷 Темы: {', '.join(impact.topics[:5])}")
    if impact.upcoming_events:
        lines.extend(["", "⏰ <b>Напоминания:</b>"])
        for ev in impact.upcoming_events:
            deadline = f" ({ev['deadline']})" if ev["deadline"] else ""
            lines.append(f"  • {sanitize_html(ev['text'])}{deadline}")
    if impact.related_contacts:
        lines.extend(["", "👥 <b>Связи:</b>"])
        for rc in impact.related_contacts[:5]:
            lines.append(
                f"  • {sanitize_html(rc['name'])} "
                f"(via: «{sanitize_html(rc['via_fact'])}»)"
            )
    if impact.direct_facts:
        lines.extend(["", "📌 <b>Факты:</b>"])
        for f in impact.direct_facts[:5]:
            snippet = sanitize_html((f.fact or "")[:80])
            lines.append(f"  • #{f.id} {snippet}")
    await message.answer("\n".join(lines))


# ─── Режим: --tag <tag> ─────────────────────────────────────────────


async def _cmd_memory_tag(message: Message, tag: str) -> None:
    """Поиск фактов по тегу."""
    from src.core.memory.memory_tagger import format_tagged, search_by_tag

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        facts = await search_by_tag(session, owner, tag)
    text = format_tagged(facts, tag)
    await message.answer(text)


# ─── Режим: --timeline [name] ───────────────────────────────────────


async def _cmd_memory_timeline(
    message: Message, userbot_manager: UserbotManager, contact_name: str
) -> None:
    """Хронология памяти (опционально отфильтрованная по контакту)."""
    contact_id, _ = await _resolve_contact(message, userbot_manager, contact_name)
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        items = await list_memories(session, owner, contact_id=contact_id)

    if not items:
        await message.answer("Память пуста.")
        return

    text = _format_timeline(items, contact_id, message.from_user.id)
    await message.answer(text)


# ─── Режим: --story <name> ──────────────────────────────────────────


async def _cmd_memory_story(
    message: Message, userbot_manager: UserbotManager, contact_name: str
) -> None:
    """История/нарратив по контакту (минимум 3 факта)."""
    contact_id, _ = await _resolve_contact(message, userbot_manager, contact_name)
    if not contact_id:
        await message.answer("Укажи контакт: <code>/memory --story имя</code>")
        return
    from src.core.memory.memory_chain import build_chain_narrative

    narrative = await build_chain_narrative(contact_id, message.from_user.id)
    if narrative:
        await message.answer(sanitize_html(narrative))
    else:
        await message.answer("Недостаточно данных для истории (нужно минимум 3 факта).")


# ─── Режим: view (default) ───────────────────────────────────────────


async def _cmd_memory_view(
    message: Message, userbot_manager: UserbotManager, contact_name: str
) -> None:
    """Показать память: всё, или про конкретный контакт, или task-факты."""
    contact_id, label = await _resolve_contact(message, userbot_manager, contact_name)

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        all_items = await list_memories(session, owner, contact_id=contact_id)
        stats = await get_memory_stats(session, owner)

        # Отделяем task-факты для показа с кнопками и статусом Commitment
        task_memories = [m for m in all_items if m.memory_type == "task"]
        task_commitments: dict[int, Commitment | None] = {}
        for m in task_memories:
            task_commitments[m.id] = await get_commitment_by_source_memory(
                session, owner.id, m.id
            )

    items = [m for m in all_items if m.memory_type != "task"]

    if not items and not task_memories:
        await message.answer("Память пуста.")
        return

    # Статистика
    pos = stats["by_sentiment"].get("positive", 0)
    neg = stats["by_sentiment"].get("negative", 0)
    neu = stats["by_sentiment"].get("neutral", 0)
    stat_line = f"🧠 <b>Память{label}</b>: {stats['total']} фактов ({pos} позитивных, {neg} негативных, {neu} нейтральных)\n"

    # Индикатор здоровья памяти
    from src.core.memory.memory_health import (
        calculate_health_score,
        format_health_compact,
    )

    health = await calculate_health_score(message.from_user.id)
    health_line = format_health_compact(health)

    # Индикатор топлива памяти
    fuel = await get_fuel_stats(message.from_user.id)
    fuel_line = format_fuel_line(fuel)
    fuel_depleted = format_depleted_contacts(fuel)

    # Отправляем task-факты отдельными сообщениями с кнопками
    for m in task_memories:
        date_str = m.created_at.strftime("%d.%m.%Y") if m.created_at else "?"
        sent = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(
            m.sentiment or "", "⚪"
        )
        c = task_commitments.get(m.id)
        if c:
            status_emoji = {
                "done": "✅",
                "cancelled": "❌",
                "open": "📋",
                "reminded": "⏰",
            }.get(c.status, "📋")
            line = (
                f"• {sent} [{date_str}] {sanitize_html(m.fact)}\n"
                f"   {status_emoji} Задача: <b>{c.status}</b>"
            )
            await message.answer(line)
        else:
            line = f"• {sent} [{date_str}] {sanitize_html(m.fact)}"
            # Кнопка создания задачи из факта памяти
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📋 Создать задачу",
                            callback_data=f"mem:totask:{m.id}",
                        ),
                    ]
                ]
            )
            await message.answer(line, reply_markup=kb)

    # Если есть только task-факты — завершаем
    if not items:
        return

    # Группировка по sentiment для остальных фактов
    positive_lines: list[str] = []
    negative_lines: list[str] = []
    neutral_lines: list[str] = []

    for m in items:
        date_str = m.created_at.strftime("%d.%m.%Y") if m.created_at else "?"
        sent = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(
            m.sentiment or "", "⚪"
        )
        rel_icon = {
            "cause": "🎯",
            "effect": "⚡",
            "contradicts": "⚠️",
            "supports": "✅",
            "continues": "➡️",
            "example_of": "📌",
        }.get(m.relation_type or "", "")
        rel_prefix = f"{rel_icon} " if rel_icon else ""
        # Distillation факты — с маркером 💡 и жирным шрифтом
        if m.source == "distillation":
            display_fact = m.fact
            if display_fact.startswith("💡 "):
                display_fact = display_fact[2:]
            line = f"• 💡 <b>{sanitize_html(display_fact)}</b>"
        else:
            line = f"• {sent} [{date_str}]{rel_prefix} {sanitize_html(m.fact)}"
        if m.sentiment == "positive":
            positive_lines.append(line)
        elif m.sentiment == "negative":
            negative_lines.append(line)
        else:
            neutral_lines.append(line)

    body_parts = [stat_line, health_line, fuel_line]
    if fuel_depleted:
        body_parts.append(fuel_depleted)
    if positive_lines:
        body_parts.append(f"\n<b>🟢 Позитивные ({len(positive_lines)}):</b>")
        body_parts.extend(positive_lines[:10])
    if negative_lines:
        body_parts.append(f"\n<b>🔴 Негативные ({len(negative_lines)}):</b>")
        body_parts.extend(negative_lines[:10])
    if neutral_lines:
        body_parts.append(f"\n<b>⚪ Нейтральные ({len(neutral_lines)}):</b>")
        body_parts.extend(neutral_lines[:10])

    body = "\n".join(body_parts)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Очистить негативные",
                    callback_data="memory:clear_negative",
                ),
                InlineKeyboardButton(
                    text="📊 Статистика", callback_data="memory:stats"
                ),
            ]
        ]
    )
    await message.answer(body, reply_markup=kb)


# ─── Утилита: resolve контакта ──────────────────────────────────────


async def _resolve_contact(
    message: Message, userbot_manager: UserbotManager, contact_name: str
) -> tuple[int | None, str]:
    """Резолвит имя контакта через userbot → (contact_id, label).

    Возвращает (None, "") если:
      - contact_name пустой
      - userbot не подключён
      - контакт не найден
    """
    if not contact_name:
        return None, ""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
    client = (
        userbot_manager.get_client(message.from_user.id) if userbot_manager else None
    )
    if client is None:
        return None, ""
    candidates = await resolve(client, owner, contact_name)
    if candidates:
        return candidates[0].peer_id, f" — {candidates[0].label()}"
    return None, ""


# ── Dreaming V3 UI: /memory --reval and /memory --correct handlers ──


@router.callback_query(F.data.startswith("memreval:"))
async def cb_memreval(callback: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает кнопки Dreaming V3: confirm/reject/permanent/cancel/rollback."""
    if callback.data is None or callback.message is None:
        await callback.answer("Ошибка")
        return
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    user_id = callback.from_user.id

    if action == "cancel":
        # Clear pending correction FSM state if the user was in it.
        current = await state.get_state()
        if current == MemoryCorrectionStates.waiting_new_text.state:
            await state.clear()
        await callback.message.edit_text("❌ Отменено.")
        await callback.answer()
        return

    # detail / rollback_all — оба обрабатываются в show_last_revals (ниже)
    if action == "detail":
        from src.core.memory.dreaming_reval_history import recent_reval_history

        text = await recent_reval_history(user_id, limit=10)
        await callback.message.answer(text)
        await callback.answer()
        return

    if action == "rollback_all":
        from src.core.memory.dreaming_reval_history import rollback_reval_history

        undone = await rollback_reval_history(user_id, limit=20)
        await callback.message.edit_text(
            f"↩️ Откатил {undone} фактов, созданных dreaming_reval."
        )
        await callback.answer()
        return

    # confirm / reject / permanent — все требуют memory_id
    if len(parts) < 3 or not parts[2].isdigit():
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    memory_id = int(parts[2])

    async with get_session() as session:
        owner = await get_or_create_user(session, user_id)
        mem = await session.get(Memory, memory_id)
        if not mem or mem.user_id != owner.id:
            await callback.answer("Факт не найден", show_alert=True)
            return

        if action == "reject":
            from src.core.memory.memory_admin import deactivate_memory

            await deactivate_memory(session, memory_id, reason="manual_reject")
            await session.commit()
            fact_text = sanitize_html(mem.fact)
            await callback.message.edit_text(f"🗑 Деактивирован: <i>{fact_text}</i>")
            await callback.answer("Факт деактивирован")
            # Clear pending correction FSM state if the user was in it.
            current = await state.get_state()
            if current == MemoryCorrectionStates.waiting_new_text.state:
                await state.clear()

        elif action == "permanent":
            from src.core.memory.memory_admin import update_memory_text

            await update_memory_text(
                session,
                memory_id,
                mem.fact,
                new_memory_type=(
                    "personal" if mem.contact_id is None else "contact_fact"
                ),
                new_decay_rate=0.01,
            )
            mem.pinned = True
            await session.commit()
            fact_text = sanitize_html(mem.fact)
            await callback.message.edit_text(
                f"♾ Сделано постоянным: <i>{fact_text}</i>"
            )
            await callback.answer("Факт сохранён навсегда")
            # Clear pending correction FSM state if the user was in it.
            current = await state.get_state()
            if current == MemoryCorrectionStates.waiting_new_text.state:
                await state.clear()

        else:
            await callback.answer("Неизвестное действие", show_alert=True)


# ─── /cancel: global handler lives in login.cmd_cancel — clears ANY FSM
# state including MemoryCorrectionStates.waiting_new_text. No need for a
# dedicated /cancel_pending here.

# ─── Режим: --history <id> ──────────────────────────────────────────


async def _cmd_memory_history(message: Message, memory_id_str: str) -> None:
    """Показать историю версий факта памяти (аудит-трейл).

    Использование: ``/memory --history <id>``
    Пример: ``/memory --history 42``
    """
    if not memory_id_str or not memory_id_str.isdigit():
        await message.answer(
            "Использование: <code>/memory --history &lt;id&gt;</code>\n"
            "Пример: <code>/memory --history 42</code>"
        )
        return

    memory_id = int(memory_id_str)

    from src.db.repos.memory_repo import get_memory_history
    from src.db.models._memory import Memory

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        mem = await session.get(Memory, memory_id)
        if not mem or mem.user_id != owner.id:
            await message.answer(f"❌ Факт #{memory_id} не найден.")
            return

        current_fact = mem.fact
        versions = await get_memory_history(session, memory_id)

    if not versions:
        await message.answer(
            f"📋 <b>История факта #{memory_id}</b>\n\n"
            f"<i>{sanitize_html(current_fact)}</i>\n\n"
            f"ℹ️ История правок пуста — факт ни разу не редактировался."
        )
        return

    lines = [
        f"📋 <b>История факта #{memory_id}</b>",
        f"📝 <b>Текущий текст:</b> <i>{sanitize_html(current_fact)}</i>",
        "",
        f"📚 <b>Версии ({len(versions)}):</b>",
    ]

    for v in versions:
        edited_at_str = (
            v.edited_at.strftime("%d.%m.%Y %H:%M") if v.edited_at else "неизвестно"
        )
        editor_label = {
            "user": "👤 пользователь",
            "system": "🤖 система",
            "agent": "🧠 агент",
        }.get(v.edited_by, f"❓ {v.edited_by}")

        reason_str = f" — {v.reason}" if v.reason else ""
        lines.append(
            f"\n<b>v{v.version}</b> [{edited_at_str}] {editor_label}{reason_str}\n"
            f"  «{sanitize_html(v.fact_text[:200])}»"
        )

    # Добавляем подсказку про откат
    lines.append(
        "\n💡 <i>Для отката используйте: "
        f"/memory --correct {memory_id} "
        "и напишите старый текст вручную.</i>"
    )

    await message.answer("\n".join(lines))


# ── Timeline format ───────────────────────────────────────────────────


def _format_timeline(
    items: list,
    contact_id: int | None,
    owner_id: int,
) -> str:
    """Форматирует факты как хронологию по неделям."""
    from datetime import datetime, timedelta, timezone
    from collections import defaultdict

    _now = datetime.now(timezone.utc)

    # Разделяем на долгосрочные (tier 3 или distillation) и обычные
    longterm = [m for m in items if m.memory_tier == 3 or m.source == "distillation"]
    regular = [m for m in items if m not in longterm and m.is_active]

    # Группируем regular факты по ISO неделям
    weekly: dict[str, list] = defaultdict(list)
    for m in regular:
        if not m.created_at:
            continue
        # Начало недели (понедельник)
        iso = m.created_at.isocalendar()
        week_start = datetime.strptime(
            f"{iso[0]}-W{iso[1]:02d}-1", "%G-W%V-%u"
        ).replace(tzinfo=timezone.utc)
        week_end = week_start + timedelta(days=6)
        label = f"{week_start.strftime('%-d')}-{week_end.strftime('%-d %b')}"
        weekly[label].append(m)

    # Сортируем недели по дате (от новых к старым)
    sorted_weeks = sorted(weekly.items(), key=lambda x: x[0], reverse=True)

    lines: list[str] = []
    if contact_id:
        # contact name is already resolved, but we don't have it here directly
        lines.append("📅 <b>История отношений:</b>\n")
    else:
        lines.append("📅 <b>Хронология памяти:</b>\n")

    for week_label, fact_list in sorted_weeks:
        # Считаем тренд недели
        pos = sum(1 for m in fact_list if m.sentiment == "positive")
        neg = sum(1 for m in fact_list if m.sentiment == "negative")
        if pos > neg:
            trend = "улучшение ⬆️"
        elif neg > pos:
            trend = "напряжение ⬇️"
        else:
            trend = "стабильно ➖"

        lines.append(f"🗓 <b>Неделя {week_label}:</b>")
        for m in fact_list[:10]:
            sent_emoji = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(
                m.sentiment or "", "⚪"
            )
            lines.append(f"  • {sent_emoji} «{sanitize_html(m.fact)}»")
        lines.append(f"  📊 Тренд: {trend}")
        lines.append("")

    # Долгосрочные факты
    if longterm:
        lines.append("🏛️ <b>Долгосрочные факты:</b>")
        for m in longterm:
            fact_text = m.fact
            if fact_text.startswith("💡 "):
                fact_text = fact_text[2:]
            lines.append(f"  • 💡 {sanitize_html(fact_text)}")
        lines.append("")

    return "\n".join(lines) if lines else "Нет данных для хронологии."
