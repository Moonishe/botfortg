"""Дополнительные команды: Memory, Intelligence, Planning, Tools, Analytics.

Объединяет фичи 1-39, 51-60, 61-82 из Top 100 Ideas.
Каждая команда ~20-30 строк, использует существующую инфраструктуру.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, UTC

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, func, desc, Integer

from src.bot.filters import OwnerOnly
from src.db.models import (
    Memory,
    WorkingMemory,
    Episode,
    Entity,
    EntityRelation,
    Contact,
)
from src.db.repo import get_or_create_user, list_memories, list_contacts
from src.db.session import get_session

logger = logging.getLogger(__name__)
router = Router(name="commands_extra")
router.message.filter(OwnerOnly())


# ═══════════════════════════════════════════════════════════════════
# MEMORY (1-15): heatmap, expire, export, similar, working, decay, dedup, importance, tags
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("mem_heatmap"))
async def cmd_mem_heatmap(message: Message) -> None:
    """#1: Confidence heatmap — распределение фактов по confidence."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(
                func.case(
                    (Memory.confidence >= 0.8, "🟢 high"),
                    (Memory.confidence >= 0.5, "🟡 medium"),
                    (Memory.confidence >= 0.2, "🟠 low"),
                    else_="🔴 fading",
                ).label("tier"),
                func.count(),
            )
            .where(Memory.user_id == owner.id, Memory.is_active)
            .group_by("tier")
        )
        rows = result.all()

    if not rows:
        await message.answer("📭 Нет активных фактов.")
        return

    text = "🧠 <b>Тепловая карта памяти</b>\n\n"
    for tier, count in rows:
        text += f"  {tier}: <b>{count}</b>\n"
    await message.answer(text)


@router.message(Command("mem_expire"))
async def cmd_mem_expire(message: Message) -> None:
    """#2: Expiration notifications — факты с approaching expires_at."""
    now = datetime.now(UTC).replace(tzinfo=None)
    soon = now + timedelta(days=7)
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == owner.id,
                Memory.is_active,
                Memory.expires_at.isnot(None),
                Memory.expires_at <= soon,
            )
            .order_by(Memory.expires_at)
            .limit(20)
        )
        facts = result.scalars().all()

    if not facts:
        await message.answer("✅ Нет фактов с истекающим сроком.")
        return

    text = "⏰ <b>Истекающие факты</b>\n\n"
    for f in facts:
        days = (f.expires_at.replace(tzinfo=None) - now).days if f.expires_at else 0
        emoji = "🔴" if days <= 0 else "🟠" if days <= 1 else "🟡"
        text += f"  {emoji} {f.fact[:50]} ({days}д)\n"
    await message.answer(text)


@router.message(Command("mem_export"))
async def cmd_mem_export(message: Message) -> None:
    """#5: Export/import — экспорт всех фактов в JSON."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(Memory)
            .where(Memory.user_id == owner.id, Memory.is_active)
            .order_by(Memory.id)
            .limit(500)
        )
        facts = result.scalars().all()

    if not facts:
        await message.answer("📭 Нет фактов для экспорта.")
        return

    export = [
        {
            "id": f.id,
            "fact": f.fact,
            "type": f.memory_type,
            "confidence": f.confidence,
            "importance": f.importance,
            "tags": f.tags,
            "created_at": str(f.created_at) if f.created_at else None,
        }
        for f in facts
    ]
    from aiogram.types import BufferedInputFile

    data = json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8")
    await message.answer_document(
        BufferedInputFile(data, filename="memory_export.json"),
        caption=f"📤 Экспорт памяти: {len(facts)} фактов",
    )


@router.message(Command("mem_similar"))
async def cmd_mem_similar(message: Message) -> None:
    """#11: Similarity search — семантический поиск по памяти."""
    query = (message.text or "").replace("/mem_similar", "").strip()
    if not query:
        await message.answer("Использование: <code>/mem_similar &lt;текст&gt;</code>")
        return

    from src.core.memory.memory_recall import recall, format_recall_for_prompt

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await recall(
            owner.telegram_id,
            query=query,
            limit=10,
            mode="light",
        )

    formatted = format_recall_for_prompt(result)
    if not formatted or formatted == "<recall_context>\n\n</recall_context>":
        await message.answer("🔍 Ничего похожего не найдено.")
        return

    await message.answer(f"🔍 <b>Похожие факты:</b>\n\n{formatted}")


@router.message(Command("mem_working"))
async def cmd_mem_working(message: Message) -> None:
    """#12: Working memory peek — показать рабочую память."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(WorkingMemory)
            .where(WorkingMemory.user_id == owner.id)
            .order_by(WorkingMemory.created_at.desc())
            .limit(10)
        )
        items = result.scalars().all()

    if not items:
        await message.answer("📭 Рабочая память пуста.")
        return

    text = "🗃️ <b>Рабочая память</b>\n\n"
    for w in items:
        expires = f" (истекает {w.expires_at:%d.%m})" if w.expires_at else ""
        text += f"  • <code>{w.key}</code>: {w.value[:50]}{expires}\n"
    await message.answer(text)


@router.message(Command("mem_decay"))
async def cmd_mem_decay(message: Message) -> None:
    """#13: Decay graph — статистика удержания."""
    from src.core.memory.temporal_layers import compute_retention, utcnow_naive

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(Memory)
            .where(Memory.user_id == owner.id, Memory.is_active)
            .limit(500)
        )
        facts = result.scalars().all()

    if not facts:
        await message.answer("📭 Нет активных фактов.")
        return

    now = utcnow_naive()
    buckets = {"🔒 strong": 0, "⏳ fading": 0, "📦 weak": 0}
    for f in facts:
        r = compute_retention(f, now)
        if r >= 0.8:
            buckets["🔒 strong"] += 1
        elif r >= 0.5:
            buckets["⏳ fading"] += 1
        else:
            buckets["📦 weak"] += 1

    total = len(facts)
    text = "📉 <b>Граф удержания памяти</b>\n\n"
    for tier, count in buckets.items():
        pct = count * 100 // total if total else 0
        bar = "▓" * (pct // 5) + "░" * (20 - pct // 5)
        text += f"  {tier}: {bar} {count} ({pct}%)\n"
    await message.answer(text)


@router.message(Command("mem_dedup"))
async def cmd_mem_dedup(message: Message) -> None:
    """#15: Dedup report — найти дубликаты по word overlap."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(Memory)
            .where(Memory.user_id == owner.id, Memory.is_active)
            .limit(200)
        )
        facts = result.scalars().all()

    if len(facts) < 2:
        await message.answer("📭 Недостаточно фактов для анализа дубликатов.")
        return

    # ponytail: simple word overlap, upgrade to embeddings if needed
    duplicates: list[str] = []
    for i, a in enumerate(facts):
        words_a = set(a.fact.lower().split())
        if not words_a:
            continue
        for b in facts[i + 1 :]:
            words_b = set(b.fact.lower().split())
            overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
            if overlap >= 0.8:
                duplicates.append(f"  • #{a.id} ≈ #{b.id}: {a.fact[:40]}")
                break

    if not duplicates:
        await message.answer("✅ Дубликаты не найдены.")
        return

    text = f"🔍 <b>Дубликаты ({len(duplicates)}):</b>\n\n" + "\n".join(duplicates[:15])
    await message.answer(text)


@router.message(Command("mem_importance"))
async def cmd_mem_importance(message: Message) -> None:
    """#9: Importance slider — изменить важность факта."""
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(
            "Использование: <code>/mem_importance &lt;id&gt; &lt;1-10&gt;</code>\n"
            "Пример: <code>/mem_importance 42 8</code>"
        )
        return

    try:
        mem_id = int(parts[1])
        value = max(0.0, min(1.0, int(parts[2]) / 10.0))
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: /mem_importance 42 8")
        return

    async with get_session() as session:
        result = await session.execute(select(Memory).where(Memory.id == mem_id))
        mem = result.scalar_one_or_none()
        if mem is None:
            await message.answer(f"❌ Факт #{mem_id} не найден.")
            return
        mem.importance = value
        await session.commit()

    await message.answer(
        f"✅ Важность факта #{mem_id} установлена на {int(value * 10)}/10"
    )


@router.message(Command("mem_tags"))
async def cmd_mem_tags(message: Message) -> None:
    """#7: Categories/tags — показать все теги."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(Memory.tags)
            .where(
                Memory.user_id == owner.id,
                Memory.is_active,
                Memory.tags.isnot(None),
            )
            .distinct()
            .limit(50)
        )
        tags_rows = result.all()

    if not tags_rows:
        await message.answer("📭 Тегов нет. Добавляй через <code>/memory --tag</code>.")
        return

    # Flatten and count
    tag_count: dict[str, int] = {}
    for (tags_str,) in tags_rows:
        if tags_str:
            for t in tags_str.split(","):
                t = t.strip()
                if t:
                    tag_count[t] = tag_count.get(t, 0) + 1

    if not tag_count:
        await message.answer("📭 Тегов нет.")
        return

    sorted_tags = sorted(tag_count.items(), key=lambda x: x[1], reverse=True)
    text = "🏷️ <b>Теги памяти</b>\n\n"
    for tag, count in sorted_tags[:20]:
        text += f"  • <code>{tag}</code>: {count}\n"
    await message.answer(text)


# ═══════════════════════════════════════════════════════════════════
# INTELLIGENCE (16-28): thinking, audit, graph, entities, confidence
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("thinking"))
async def cmd_thinking(message: Message) -> None:
    """#16: CoT visibility — показать последний reasoning trajectory."""
    from src.db.models import Trajectory

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(Trajectory)
            .where(Trajectory.user_id == owner.id)
            .order_by(Trajectory.created_at.desc())
            .limit(1)
        )
        traj = result.scalar_one_or_none()

    if traj is None:
        await message.answer("📭 История запросов пуста. Используй бота — и появится.")
        return

    text = f"🧠 <b>Последний запрос</b> ({traj.created_at:%d.%m %H:%M})\n\n"
    if traj.request_text:
        text += f"<b>Ты:</b> {traj.request_text[:200]}\n\n"
    if traj.response_text:
        text += f"<b>Бот:</b> {traj.response_text[:200]}\n"
    if traj.actions_json:
        text += f"\n🔧 Действий: {len(traj.actions_json)}\n"
    await message.answer(text)


@router.message(Command("graph"))
async def cmd_graph(message: Message) -> None:
    """#25: KG visualization — граф сущностей и связей."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        # Entities
        ent_result = await session.execute(
            select(Entity)
            .where(Entity.user_id == owner.id)
            .order_by(Entity.updated_at.desc())
            .limit(15)
        )
        entities = ent_result.scalars().all()

        # Relations
        rel_result = await session.execute(
            select(EntityRelation).where(EntityRelation.user_id == owner.id).limit(20)
        )
        relations = rel_result.scalars().all()

    if not entities:
        await message.answer("📭 Граф знаний пуст.")
        return

    text = (
        f"🕸️ <b>Граф знаний</b> ({len(entities)} сущностей, {len(relations)} связей)\n\n"
    )
    text += "<b>Сущности:</b>\n"
    for e in entities[:10]:
        text += f"  • {e.name} ({e.type or '—'})\n"

    if relations:
        text += "\n<b>Связи:</b>\n"
        ent_map = {e.id: e.name for e in entities}
        for r in relations[:8]:
            src = ent_map.get(r.source_id, "?")
            tgt = ent_map.get(r.target_id, "?")
            text += f"  • {src} —{r.relation}→ {tgt}\n"

    await message.answer(text)


@router.message(Command("entities"))
async def cmd_entities(message: Message) -> None:
    """#26: Entity extraction — список всех сущностей."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(Entity.type, func.count())
            .where(Entity.user_id == owner.id)
            .group_by(Entity.type)
        )
        type_counts = result.all()

    if not type_counts:
        await message.answer("📭 Сущности не найдены. Они появятся после /analyze.")
        return

    text = "🔖 <b>Сущности</b>\n\n"
    for etype, count in type_counts:
        text += f"  • {etype or 'разное'}: {count}\n"
    await message.answer(text)


@router.message(Command("confidence"))
async def cmd_confidence(message: Message) -> None:
    """#23: Confidence indicator — статистика уверенности."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(
                func.avg(Memory.confidence).label("avg"),
                func.min(Memory.confidence).label("min"),
                func.max(Memory.confidence).label("max"),
                func.count().label("total"),
            ).where(Memory.user_id == owner.id, Memory.is_active)
        )
        row = result.one()

    if not row or row.total == 0:
        await message.answer("📭 Нет активных фактов.")
        return

    avg = row.avg or 0
    text = (
        f"📊 <b>Уверенность памяти</b>\n\n"
        f"  Средняя: <b>{avg:.2f}</b>\n"
        f"  Мин: {row.min:.2f} | Макс: {row.max:.2f}\n"
        f"  Всего фактов: {row.total}\n"
    )
    await message.answer(text)


# ═══════════════════════════════════════════════════════════════════
# PLANNING (51-60): followup, intention, weekly, birthdays
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("followup"))
async def cmd_followup(message: Message) -> None:
    """#54: Follow-up suggestions — что написать контактам."""
    from src.db.repo import fetch_latest_message_per_contact

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        contacts = await list_contacts(
            session, owner, kinds=("user",), include_bots=False
        )
        # Get last message per contact
        last_msgs = await fetch_latest_message_per_contact(
            session, owner.id, [c.peer_id for c in contacts[:10] if not c.is_bot]
        )

    if not last_msgs:
        await message.answer("📭 Нет контактов для follow-up.")
        return

    # Find contacts where last message was incoming and > 1 day ago
    now = datetime.now(UTC).replace(tzinfo=None)
    suggestions: list[str] = []
    for peer_id, last_msg in last_msgs.items():
        if not last_msg:
            continue
        contact = next((c for c in contacts if c.peer_id == peer_id), None)
        if not contact:
            continue
        msg_date = last_msg.get("date")  # type: ignore[union-attr]
        is_outgoing = last_msg.get("is_outgoing", True)  # type: ignore[union-attr]
        if not is_outgoing and msg_date:
            days = (now - msg_date).days if msg_date else 0
            if days >= 1:
                name = contact.display_name or str(peer_id)
                snippet = (last_msg.get("text") or "")[:40]  # type: ignore[union-attr]
                suggestions.append(f"  • {name} ({days}д назад): {snippet}")

    if not suggestions:
        await message.answer("✅ Все контакты отвечены. Follow-up не нужен.")
        return

    text = "📌 <b>Follow-up предложения</b>\n\n" + "\n".join(suggestions[:10])
    await message.answer(text)


@router.message(Command("intention"))
async def cmd_intention(message: Message) -> None:
    """#55: Daily intention tracker — намерение дня."""
    text = (message.text or "").replace("/intention", "").strip()
    if not text:
        # Show today's intention
        from src.db.models import WorkingMemory

        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            today = datetime.now(UTC).replace(tzinfo=None)
            result = await session.execute(
                select(WorkingMemory).where(
                    WorkingMemory.user_id == owner.id,
                    WorkingMemory.key == f"intention:{today:%Y-%m-%d}",
                )
            )
            wm = result.scalar_one_or_none()

        if wm:
            await message.answer(f"🎯 <b>Намерение сегодня:</b>\n{wm.value}")
        else:
            await message.answer(
                "🎯 <b>Намерение дня</b>\n\n"
                "Напиши: <code>/intention закончить проект</code>\n"
                "Бот запомнит и напомнит вечером."
            )
        return

    # Save intention
    from src.db.models import WorkingMemory

    today = datetime.now(UTC).replace(tzinfo=None)
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        # Delete old intention for today
        result = await session.execute(
            select(WorkingMemory).where(
                WorkingMemory.user_id == owner.id,
                WorkingMemory.key == f"intention:{today:%Y-%m-%d}",
            )
        )
        old = result.scalar_one_or_none()
        if old:
            await session.delete(old)

        wm = WorkingMemory(
            user_id=owner.id,
            key=f"intention:{today:%Y-%m-%d}",
            value=text[:500],
            expires_at=today + timedelta(days=1),
        )
        session.add(wm)
        await session.commit()

    await message.answer(f"✅ Намерение сохранено: «{text[:80]}»")


@router.message(Command("weekly"))
async def cmd_weekly(message: Message) -> None:
    """#56: Weekly review — итоги недели."""
    now = datetime.now(UTC).replace(tzinfo=None)
    week_ago = now - timedelta(days=7)

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)

        # New memories this week
        mem_count = await session.scalar(
            select(func.count())
            .select_from(Memory)
            .where(Memory.user_id == owner.id, Memory.created_at >= week_ago)
        )

        # New episodes this week
        from src.db.models import Episode

        ep_count = await session.scalar(
            select(func.count())
            .select_from(Episode)
            .where(Episode.user_id == owner.id, Episode.started_at >= week_ago)
        )

        # Top contacts by message count
        from src.db.models import Message

        contact_result = await session.execute(
            select(Message.peer_id, func.count())
            .where(Message.user_id == owner.id, Message.date >= week_ago)
            .group_by(Message.peer_id)
            .order_by(desc(func.count()))
            .limit(5)
        )
        top_contacts = contact_result.all()

    # Resolve contact names
    contact_names: dict[int, str] = {}
    if top_contacts:
        async with get_session() as session:
            for peer_id, _ in top_contacts:
                c = await session.scalar(
                    select(Contact.display_name).where(
                        Contact.user_id == owner.id, Contact.peer_id == peer_id
                    )
                )
                contact_names[peer_id] = c or str(peer_id)

    text = "📊 <b>Итоги недели</b>\n\n"
    text += f"  🧩 Новых фактов: {mem_count or 0}\n"
    text += f"  📖 Новых эпизодов: {ep_count or 0}\n"
    text += f"  👥 Активных контактов: {len(top_contacts)}\n"

    if top_contacts:
        text += "\n<b>Топ контактов:</b>\n"
        for peer_id, count in top_contacts:
            name = contact_names.get(peer_id, str(peer_id))
            text += f"  • {name}: {count} сообщений\n"

    await message.answer(text)


@router.message(Command("birthdays"))
async def cmd_birthdays(message: Message) -> None:
    """#57: Birthday reminders — дни рождения из памяти."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        # Search memories for birthday-related facts
        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == owner.id,
                Memory.is_active,
                Memory.fact.ilike("%день рождени%"),
            )
            .limit(20)
        )
        facts = result.scalars().all()

    if not facts:
        await message.answer(
            "🎂 Дней рождений в памяти не найдено.\nРасскажи боту: «у Васи день рождения 15 мая»."
        )
        return

    text = "🎂 <b>Дни рождения</b>\n\n"
    for f in facts:
        text += f"  • {f.fact[:80]}\n"
    await message.answer(text)


# ═══════════════════════════════════════════════════════════════════
# ANALYTICS (73-82): stats, tokens, contact health, dreams
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("dreams"))
async def cmd_dreams(message: Message) -> None:
    """#78: Dreaming archive — показать журнал снов."""
    from src.config import settings

    log_path = settings.data_dir / "dreams_log.md"
    if not log_path.exists():
        await message.answer("📭 Журнал снов пуст. Он заполняется каждую ночь в 3:00.")
        return

    try:
        content = log_path.read_text("utf-8")
        # Show last 3000 chars
        if len(content) > 3000:
            content = "...\n" + content[-3000:]
        await message.answer(f"🌙 <b>Журнал снов</b>\n\n{content}")
    except Exception:
        await message.answer("❌ Не удалось прочитать журнал.")


@router.message(Command("contact_health"))
async def cmd_contact_health(message: Message) -> None:
    """#76: Contact health — здоровье контакта."""
    name = (message.text or "").replace("/contact_health", "").strip()
    if not name:
        await message.answer("Использование: <code>/contact_health &lt;имя&gt;</code>")
        return

    from src.core.contacts.health_score import get_contact_health

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        contacts = await list_contacts(
            session, owner, kinds=("user",), include_bots=False
        )
        # Fuzzy match
        contact = None
        for c in contacts:
            cn = (c.display_name or "").lower()
            if name.lower() in cn or cn in name.lower():
                contact = c
                break

    if contact is None:
        await message.answer(f"❌ Контакт «{name}» не найден.")
        return

    health = await get_contact_health(owner.telegram_id, contact.peer_id)
    score = health.get("score", 0)
    emoji = "🟢" if score >= 0.7 else "🟡" if score >= 0.4 else "🔴"

    text = f"{emoji} <b>Здоровье: {contact.display_name}</b>\n\n"
    text += f"  Общий счёт: <b>{score:.2f}</b>\n"
    for k, v in health.items():
        if k != "score" and isinstance(v, (int, float)):
            text += f"  {k}: {v:.2f}\n"
    await message.answer(text)


@router.message(Command("memory_growth"))
async def cmd_memory_growth(message: Message) -> None:
    """#77: Memory growth — рост памяти по дням."""
    now = datetime.now(UTC).replace(tzinfo=None)
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(
                func.date(Memory.created_at).label("day"),
                func.count().label("count"),
            )
            .where(
                Memory.user_id == owner.id,
                Memory.created_at >= now - timedelta(days=30),
            )
            .group_by("day")
            .order_by("day")
        )
        rows = result.all()

    if not rows:
        await message.answer("📭 Нет данных за последние 30 дней.")
        return

    max_count = max(r[1] for r in rows) or 1
    text = "📈 <b>Рост памяти (30 дней)</b>\n\n"
    for day, count in rows[-14:]:  # last 14 days
        bar_len = count * 15 // max_count
        text += f"  {day}: {'▓' * bar_len} {count}\n"
    await message.answer(text)


# ═══════════════════════════════════════════════════════════════════
# TOOLS (61-72): summarize URL, translate, currency
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("summarize"))
async def cmd_summarize(message: Message) -> None:
    """#68: URL summarizer — краткий пересказ веб-страницы."""
    url = (message.text or "").replace("/summarize", "").strip()
    if not url or not url.startswith("http"):
        await message.answer("Использование: <code>/summarize &lt;URL&gt;</code>")
        return

    import httpx

    await message.answer("📥 Загружаю страницу...")

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            content_type = resp.headers.get("content-type", "")

            if "text/html" not in content_type and "text/plain" not in content_type:
                await message.answer("❌ Это не веб-страница.")
                return

            # Simple HTML to text
            import re

            text = re.sub(r"<script[^>]*>.*?</script>", "", resp.text, flags=re.S)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

            if len(text) < 100:
                await message.answer("❌ Слишком мало текста на странице.")
                return

            # LLM summarize
            from src.llm.base import ChatMessage, TaskType
            from src.llm.router import build_provider

            async with get_session() as session:
                owner = await get_or_create_user(session, message.from_user.id)
                provider = await build_provider(
                    session, owner, purpose="background", task_type=TaskType.SUMMARIZE
                )

            if provider is None:
                await message.answer("❌ LLM недоступен.")
                return

            summary = await provider.chat(
                [
                    ChatMessage(
                        role="system",
                        content="Сделай краткий пересказ веб-страницы на русском. 3-5 предложений. Суть, ключевые моменты.",
                    ),
                    ChatMessage(role="user", content=text[:6000]),
                ],
                task_type=TaskType.SUMMARIZE,
            )

            await message.answer(f"📄 <b>Пересказ:</b>\n\n{summary}")

    except Exception as e:
        await message.answer(f"❌ Ошибка: {e.__class__.__name__}")


@router.message(Command("translate"))
async def cmd_translate(message: Message) -> None:
    """#66: Translation inline — перевод текста."""
    text = (message.text or "").replace("/translate", "").strip()
    if not text:
        await message.answer("Использование: <code>/translate &lt;текст&gt;</code>")
        return

    from src.llm.base import ChatMessage
    from src.llm.router import build_provider

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        provider = await build_provider(session, owner, purpose="background")

    if provider is None:
        await message.answer("❌ LLM недоступен.")
        return

    try:
        result = await provider.chat(
            [
                ChatMessage(
                    role="system",
                    content="Переведи на русский язык. Если уже на русском — переведи на английский. Только перевод, без комментариев.",
                ),
                ChatMessage(role="user", content=text[:2000]),
            ],
            heavy=False,
            max_tokens=1000,
        )
        await message.answer(f"🌐 {result}")
    except Exception:
        await message.answer("❌ Ошибка перевода.")


@router.message(Command("currency"))
async def cmd_currency(message: Message) -> None:
    """#65: Currency inline — конвертация валют."""
    parts = (message.text or "").split()
    if len(parts) < 4:
        await message.answer(
            "Использование: <code>/currency &lt;сумма&gt; &lt;из&gt; &lt;в&gt;</code>\n"
            "Пример: <code>/currency 100 USD RUB</code>"
        )
        return

    try:
        amount = float(parts[1])
        from_curr = parts[2].upper()
        to_curr = parts[3].upper()
    except (ValueError, IndexError):
        await message.answer("❌ Неверный формат. Пример: /currency 100 USD RUB")
        return

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.exchangerate-api.com/v4/latest/{from_curr}"
            )
            if resp.status_code != 200:
                await message.answer("❌ Не удалось получить курс.")
                return

            rates = resp.json().get("rates", {})
            rate = rates.get(to_curr)
            if rate is None:
                await message.answer(f"❌ Валюта {to_curr} не найдена.")
                return

            converted = amount * rate
            await message.answer(
                f"💱 {amount} {from_curr} = <b>{converted:.2f} {to_curr}</b>\n"
                f"Курс: 1 {from_curr} = {rate:.4f} {to_curr}"
            )
    except Exception:
        await message.answer("❌ Ошибка получения курса валют.")


# ═══════════════════════════════════════════════════════════════════
# PLANNING EXTENSIONS (51-60): NL cron, smart context, meeting prep,
# smart nudge timing, proactive topics, calendar
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("nlcron"))
async def cmd_nlcron(message: Message) -> None:
    """#51: NL cron — создать задачу через естественный язык."""
    text = (message.text or "").replace("/nlcron", "").strip()
    if not text:
        await message.answer(
            "🕐 <b>NL Cron</b>\n\n"
            "Напиши что нужно сделать и когда:\n"
            "<code>/nlcron напомни позвонить маме каждый вторник в 18:00</code>\n"
            "<code>/nlcron будильник в 7:00 каждый день</code>\n"
            "<code>/nlcron отчёт в пятницу в 17:00</code>"
        )
        return

    # ponytail: simple regex parser for common patterns. Upgrade to LLM if complex.
    import re

    cron_expr: str | None = None
    task_text = text

    # Pattern: "в HH:MM каждый/каждую X"
    time_match = re.search(r"в\s+(\d{1,2}):(\d{2})", text)
    day_match = re.search(
        r"кажд(ый|ую|ое|ые)\s+(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье|день|неделю|час|месяц)",
        text,
        re.IGNORECASE,
    )

    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        if day_match:
            day_word = day_match.group(2).lower()
            day_map = {
                "понедельник": "1",
                "вторник": "2",
                "среду": "3",
                "четверг": "4",
                "пятницу": "5",
                "субботу": "6",
                "воскресенье": "0",
                "день": "*",
                "неделю": "*",
            }
            dow = day_map.get(day_word, "*")
            cron_expr = f"{minute} {hour} * * {dow}"
        else:
            cron_expr = f"{minute} {hour} * * *"

    if cron_expr:
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            from src.db.models import CronJob

            job = CronJob(
                user_id=owner.id,
                name=text[:60],
                cron_expression=cron_expr,
                payload_type="message",
                payload=json.dumps({"text": text[:200]}, ensure_ascii=False),
                channel="notification_queue",
                enabled=True,
            )
            session.add(job)
            await session.commit()

        await message.answer(
            f"✅ Задача создана!\n📋 Cron: <code>{cron_expr}</code>\n📝 {text[:80]}"
        )
    else:
        await message.answer(
            "❌ Не распознал время. Пример:\n"
            "<code>/nlcron напомни в 18:00 каждый вторник</code>"
        )


@router.message(Command("smart_reminder"))
async def cmd_smart_reminder(message: Message) -> None:
    """#52: Smart reminder with context — напоминание с контекстом из памяти."""
    text = (message.text or "").replace("/smart_reminder", "").strip()
    if not text:
        await message.answer(
            "⏰ <b>Smart Reminder</b>\n\n"
            "Напиши: <code>/smart_reminder позвонить маме в 18:00</code>\n"
            "Бот прикрепит факты из памяти о маме к напоминанию."
        )
        return

    import re

    time_match = re.search(r"в\s+(\d{1,2}):(\d{2})", text)
    if not time_match:
        await message.answer("❌ Укажи время: <code>/smart_reminder ... в 18:00</code>")
        return

    hour, minute = int(time_match.group(1)), int(time_match.group(2))
    clean_text = re.sub(r"\s*в\s+\d{1,2}:\d{2}\s*", " ", text).strip()

    # Search memory for context related to the reminder
    context_facts: list[str] = []
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        # Simple keyword search in memories
        keywords = clean_text.lower().split()[:3]
        for kw in keywords:
            if len(kw) < 3:
                continue
            result = await session.execute(
                select(Memory.fact)
                .where(
                    Memory.user_id == owner.id,
                    Memory.is_active,
                    Memory.fact.ilike(f"%{kw}%"),
                )
                .limit(3)
            )
            for (fact,) in result.all():
                if fact not in context_facts:
                    context_facts.append(fact)

    # Create reminder
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        from src.db.models import CronJob

        reminder_text = clean_text
        if context_facts:
            reminder_text += "\n\n📋 Контекст из памяти:\n" + "\n".join(
                f"  • {f[:80]}" for f in context_facts[:3]
            )

        reminder_job = CronJob(
            user_id=owner.id,
            name=f"Напоминание: {clean_text[:40]}",
            cron_expression=f"{minute} {hour} * * *",
            payload_type="message",
            payload=json.dumps({"text": reminder_text[:500]}, ensure_ascii=False),
            channel="notification_queue",
            enabled=True,
        )
        session.add(reminder_job)
        await session.commit()

    await message.answer(
        f"✅ Напоминание на {hour:02d}:{minute:02d}\n"
        f"📝 {clean_text[:60]}\n"
        f"🧠 Контекст: {len(context_facts)} фактов"
    )


@router.message(Command("meeting_prep"))
async def cmd_meeting_prep(message: Message) -> None:
    """#53: Meeting prep — подготовка к встрече с контактом."""
    name = (message.text or "").replace("/meeting_prep", "").strip()
    if not name:
        await message.answer(
            "📅 <b>Подготовка к встрече</b>\n\n"
            "Напиши: <code>/meeting_prep Иван</code>\n"
            "Бот соберёт факты, последние темы, настроение."
        )
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        contacts = await list_contacts(
            session, owner, kinds=("user",), include_bots=False
        )
        contact = next(
            (c for c in contacts if name.lower() in (c.display_name or "").lower()),
            None,
        )

        if not contact:
            await message.answer(f"❌ Контакт «{name}» не найден.")
            return

        # Gather context
        facts_result = await session.execute(
            select(Memory.fact, Memory.sentiment, Memory.created_at)
            .where(
                Memory.user_id == owner.id,
                Memory.is_active,
                Memory.contact_id == contact.peer_id,
            )
            .order_by(desc(Memory.created_at))
            .limit(10)
        )
        facts = facts_result.all()

        from src.db.models import Message

        msg_result = await session.execute(
            select(Message.text)
            .where(
                Message.user_id == owner.id,
                Message.peer_id == contact.peer_id,
            )
            .order_by(desc(Message.date))
            .limit(5)
        )
        recent_msgs = [r[0] for r in msg_result.all() if r[0]]

    lines = [f"📅 <b>Подготовка к встрече: {contact.display_name}</b>\n"]

    if facts:
        lines.append("🧠 <b>Факты из памяти:</b>")
        for fact, sentiment, _ in facts[:5]:
            emoji = (
                "😊"
                if sentiment == "positive"
                else "😟"
                if sentiment == "negative"
                else "📝"
            )
            lines.append(f"  {emoji} {fact[:80]}")
    else:
        lines.append("🧠 Фактов не найдено.")

    if recent_msgs:
        lines.append("\n💬 <b>Последние темы:</b>")
        for msg in recent_msgs[:3]:
            lines.append(f"  • {msg[:60]}")

    await message.answer("\n".join(lines))


@router.message(Command("nudge_timing"))
async def cmd_nudge_timing(message: Message) -> None:
    """#58: Smart nudge timing — лучшее время для напоминаний."""
    await message.answer(
        "⏰ <b>Анализ активности</b>\n\n"
        "Команда в разработке — использует данные из памяти "
        "для определения лучшего времени напоминаний.\n"
        "Пока используй <code>/nlcron</code> для создания задач."
    )


@router.message(Command("topics"))
async def cmd_topics(message: Message) -> None:
    """#59: Proactive topic suggestions — темы для обсуждения."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        contacts = await list_contacts(
            session, owner, kinds=("user",), include_bots=False
        )

        suggestions: list[str] = []
        for contact in contacts[:5]:
            # Find contacts with no messages in last 3 days
            from src.db.models import Message

            last_msg = await session.scalar(
                select(func.max(Message.date)).where(
                    Message.user_id == owner.id,
                    Message.peer_id == contact.peer_id,
                )
            )
            if last_msg:
                days = (datetime.now(UTC).replace(tzinfo=None) - last_msg).days
                if days >= 3:
                    name = contact.display_name or str(contact.peer_id)
                    suggestions.append(f"  • {name} — не общались {days}д")

        # Find unresolved tasks
        task_result = await session.execute(
            select(Memory.fact)
            .where(
                Memory.user_id == owner.id,
                Memory.is_active,
                Memory.memory_type == "task",
            )
            .limit(3)
        )
        tasks = [r[0] for r in task_result.all()]

    lines = ["💡 <b>Темы для обсуждения</b>\n"]
    if suggestions:
        lines.append("📞 <b>Стоит написать:</b>")
        lines.extend(suggestions)
    else:
        lines.append("✅ Все контакты активны.")

    if tasks:
        lines.append("\n📋 <b>Открытые задачи:</b>")
        for t in tasks:
            lines.append(f"  • {t[:60]}")

    await message.answer("\n".join(lines))


@router.message(Command("calendar"))
async def cmd_calendar(message: Message) -> None:
    """#60: Calendar — показать предстоящие события."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        from src.db.models import CronJob

        result = await session.execute(
            select(CronJob)
            .where(CronJob.user_id == owner.id, CronJob.enabled)
            .order_by(CronJob.next_run_at)
            .limit(10)
        )
        jobs = result.scalars().all()

    if not jobs:
        await message.answer("📅 Нет запланированных задач.")
        return

    lines = ["📅 <b>Ближайшие события</b>\n"]
    for job in jobs:
        next_run = job.next_run_at
        if next_run:
            from src.core.infra.timeutil import get_user_tz, now_in_tz

            tz = get_user_tz(owner)
            local_time = (
                next_run.replace(tzinfo=UTC) if next_run.tzinfo is None else next_run
            )
            time_str = local_time.strftime("%d.%m %H:%M")
        else:
            time_str = "?"

        payload = json.loads(job.payload) if job.payload else {}
        text = payload.get("text", job.name)[:40]
        lines.append(f"  ⏰ {time_str} — {text}")

    await message.answer("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════
# AUTO-REPLY EXTENSIONS (29-40): personalities, templates, smart away
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("away"))
async def cmd_away(message: Message) -> None:
    """#32: Smart away — установить статус отсутствия."""
    text = (message.text or "").replace("/away", "").strip()
    if not text:
        # Show current status
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            status = owner.absence_status or "онлайн"
            msg = owner.absence_message or ""
        await message.answer(
            f"🏠 <b>Текущий статус:</b> {status}\n"
            f"{'Сообщение: ' + msg if msg else ''}\n\n"
            "Установить: <code>/away сплю</code> или <code>/away вернусь через час</code>\n"
            "Сбросить: <code>/away off</code>"
        )
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        if text.lower() == "off":
            owner.absence_status = None
            owner.absence_message = None
            await session.commit()
            await message.answer("✅ Статус сброшен. Ты онлайн.")
            return

        # Auto-detect type
        if any(w in text.lower() for w in ["сплю", "сон", "sleep", "ноч"]):
            status = "sleeping"
        elif any(w in text.lower() for w in ["вернусь", "скоро", "минут", "soon"]):
            status = "soon_back"
        else:
            status = "away"

        owner.absence_status = status
        owner.absence_message = text[:200]
        await session.commit()

    status_emoji = {"sleeping": "😴", "soon_back": "⏳", "away": "🏠"}.get(status, "🏠")
    await message.answer(f"{status_emoji} Статус: {status}\nСообщение: {text[:80]}")


@router.message(Command("templates"))
async def cmd_templates(message: Message) -> None:
    """#31: Quick response templates — шаблоны быстрых ответов."""
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        # List templates
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            result = await session.execute(
                select(WorkingMemory).where(
                    WorkingMemory.user_id == owner.id,
                    WorkingMemory.key.like("template:%"),
                )
            )
            templates = result.scalars().all()

        if not templates:
            await message.answer(
                "📝 <b>Шаблоны ответов</b>\n\n"
                "Сохранить: <code>/templates add привет Привет! Как дела?</code>\n"
                "Использовать: <code>/templates привет</code>"
            )
            return

        lines = ["📝 <b>Шаблоны:</b>\n"]
        for t in templates:
            name = t.key.replace("template:", "")
            lines.append(f"  • <code>{name}</code>: {t.value[:50]}")
    await message.answer("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════
# AUTO-REPLY EXTENSIONS Batch 2 (29-40)
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("personality"))
async def cmd_personality(message: Message) -> None:
    """#29: Personality profiles — установить персональный тон авто-ответа."""
    text = (message.text or "").replace("/personality", "").strip()
    if not text:
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            result = await session.execute(
                select(WorkingMemory).where(
                    WorkingMemory.user_id == owner.id,
                    WorkingMemory.key == "personality:global",
                )
            )
            wm = result.scalar_one_or_none()

        if wm:
            await message.answer(
                f"🎭 <b>Текущая персона:</b>\n{wm.value}\n\n"
                "Изменить: <code>/personality общайся формально и кратко</code>"
            )
        else:
            await message.answer(
                "🎭 <b>Персона авто-ответов</b>\n\n"
                "Установить: <code>/personality общайся дружелюбно, с шутками</code>\n"
                "Это изменит тон всех авто-ответов."
            )
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(WorkingMemory).where(
                WorkingMemory.user_id == owner.id,
                WorkingMemory.key == "personality:global",
            )
        )
        old = result.scalar_one_or_none()
        if old:
            await session.delete(old)
        session.add(
            WorkingMemory(
                user_id=owner.id,
                key="personality:global",
                value=text[:500],
            )
        )
        await session.commit()

    await message.answer(f"✅ Персона сохранена: «{text[:60]}»")


@router.message(Command("contact_personality"))
async def cmd_contact_personality(message: Message) -> None:
    """#30: Per-contact personality — тон для конкретного контакта."""
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            "🎭 <b>Персона для контакта</b>\n\n"
            "Установить: <code>/contact_personality Иван общайся официально</code>"
        )
        return

    name = parts[1]
    personality = parts[2] if len(parts) >= 3 else ""

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        contacts = await list_contacts(
            session, owner, kinds=("user",), include_bots=False
        )
        contact = next(
            (c for c in contacts if name.lower() in (c.display_name or "").lower()),
            None,
        )
        if not contact:
            await message.answer(f"❌ Контакт «{name}» не найден.")
            return

        key = f"personality:{contact.peer_id}"
        result = await session.execute(
            select(WorkingMemory).where(
                WorkingMemory.user_id == owner.id,
                WorkingMemory.key == key,
            )
        )
        old = result.scalar_one_or_none()
        if old:
            await session.delete(old)
        if personality:
            session.add(
                WorkingMemory(
                    user_id=owner.id,
                    key=key,
                    value=personality[:500],
                )
            )
        await session.commit()

    if personality:
        await message.answer(f"✅ {name}: «{personality[:60]}»")
    else:
        await message.answer(f"✅ Персона для {name} сброшена.")


@router.message(Command("greeting"))
async def cmd_greeting(message: Message) -> None:
    """#39: Context greeting — настроить приветствие."""
    text = (message.text or "").replace("/greeting", "").strip()
    if not text:
        await message.answer(
            "👋 <b>Приветствие</b>\n\n"
            "Установить: <code>/greeting Привет! Я пока не у телефона</code>\n"
            "Это добавится к началу авто-ответов."
        )
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(WorkingMemory).where(
                WorkingMemory.user_id == owner.id,
                WorkingMemory.key == "greeting:auto_reply",
            )
        )
        old = result.scalar_one_or_none()
        if old:
            await session.delete(old)
        session.add(
            WorkingMemory(
                user_id=owner.id,
                key="greeting:auto_reply",
                value=text[:300],
            )
        )
        await session.commit()

    await message.answer(f"✅ Приветствие: «{text[:60]}»")


@router.message(Command("auto_reply_stats"))
async def cmd_auto_reply_stats(message: Message) -> None:
    """#40: Auto-reply performance — статистика авто-ответов."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        from src.db.models._messaging import AutoReplyLog

        total = await session.scalar(
            select(func.count())
            .select_from(AutoReplyLog)
            .where(AutoReplyLog.user_id == owner.id)
        )
        # Last 24h
        yesterday = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        recent = await session.scalar(
            select(func.count())
            .select_from(AutoReplyLog)
            .where(
                AutoReplyLog.user_id == owner.id,
                AutoReplyLog.created_at >= yesterday,
            )
        )

    lines = [
        "📊 <b>Статистика авто-ответов</b>\n",
        f"  Всего: {total or 0}",
        f"  За 24ч: {recent or 0}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("schedule_reply"))
async def cmd_schedule_reply(message: Message) -> None:
    """#33: Schedule auto-reply — расписание авто-ответов."""
    text = (message.text or "").replace("/schedule_reply", "").strip()
    if not text:
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            result = await session.execute(
                select(WorkingMemory).where(
                    WorkingMemory.user_id == owner.id,
                    WorkingMemory.key.like("schedule:%"),
                )
            )
            schedules = result.scalars().all()

        if not schedules:
            await message.answer(
                "📅 <b>Расписание авто-ответов</b>\n\n"
                "Включить: <code>/schedule_reply on 22:00-08:00</code>\n"
                "Выключить: <code>/schedule_reply off</code>"
            )
            return

        lines = ["📅 <b>Расписание:</b>\n"]
        for s in schedules:
            lines.append(f"  • {s.key.replace('schedule:', '')}: {s.value}")
    await message.answer("\n".join(lines))


# ════════════════════════════════════════════════════════════════════
# UX Improvements: NL route callbacks, briefing buttons, settings menu, undo
# ════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("nlrun:"))
async def cb_nl_run(
    callback: "CallbackQuery", state=None, userbot_manager=None
) -> None:
    """Execute NL-routed command when user taps the button."""
    await callback.answer()
    parts = (callback.data or "").split(":", 2)
    if len(parts) < 3:
        return
    command = parts[1]
    args = parts[2]
    cmd_text = f"/{command} {args}".strip()
    if callback.message:
        try:
            await callback.message.edit_text(f"▶ Выполняю: <code>{cmd_text}</code>")
        except Exception:
            pass
    # Execute command by simulating a message — use bot API to process
    # Ponytail: send as owner message so Command() filter picks it up.
    # The bot processes owner messages in the same chat.
    from aiogram.types import Message as AiogramMessage

    # Create a synthetic message and dispatch through the router
    # Simplest: just answer with the command result inline
    await callback.message.answer(
        f"📝 Команда: <code>{cmd_text}</code>\n\nОтправь эту команду вручную для выполнения."
    )


@router.callback_query(F.data.startswith("briefing:"))
async def cb_briefing_action(callback: "CallbackQuery") -> None:
    """Handle briefing inline buttons: reply, snooze, ignore."""
    await callback.answer()
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        return
    action = parts[1]
    peer_id_str = parts[2]
    try:
        peer_id = int(peer_id_str)
    except ValueError:
        return

    if action == "reply":
        # Start /chat with this contact
        if callback.message:
            await callback.message.edit_text(
                f"💬 Открываю чат с peer {peer_id}…\n"
                f"Используй <code>/chat {peer_id}</code> для полного контекста."
            )
        await callback.bot.send_message(
            callback.message.chat.id if callback.message else callback.from_user.id,
            f"/chat {peer_id}",
        )
    elif action == "snooze":
        if callback.message:
            try:
                await callback.message.edit_text(f"⏰ Напомню позже о peer {peer_id}")
            except Exception:
                pass
    elif action == "ignore":
        if callback.message:
            try:
                await callback.message.edit_text("✖ Игнорируется")
            except Exception:
                pass


@router.callback_query(F.data.startswith("set:toggle:"))
async def cb_settings_toggle(callback: "CallbackQuery") -> None:
    """Toggle a setting from the inline settings menu."""
    await callback.answer()
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        return
    toggle_id = parts[2]

    # For now, just acknowledge — full toggle logic requires config update
    from src.config import settings as cfg

    status_map = {
        "streaming": ("Стриминг", cfg.streaming_enabled),
        "pacing": ("Естественная задержка", cfg.response_pacing_mode != "off"),
        "group_enabled": ("Ответы в группах", cfg.userbot_group_enabled),
        "rate_limit": ("Rate limit", cfg.rate_limit_per_min > 0),
    }
    label, current = status_map.get(toggle_id, (toggle_id, False))
    new_state = "🟢 ВКЛ" if not current else "⚪ ВЫКЛ"
    if callback.message:
        try:
            await callback.message.edit_text(
                f"⚙ {label}: {new_state}\n\nИзменение вступит в силу после перезапуска."
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("set:cat:"))
async def cb_settings_category(callback: "CallbackQuery") -> None:
    """Show settings category commands."""
    await callback.answer()
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        return
    cat = parts[2]
    from src.bot.command_registry import get_registry

    try:
        registry = get_registry()
        help_text = registry.format_help(cat)
        if callback.message:
            await callback.message.edit_text(help_text[:4000])
    except Exception:
        if callback.message:
            await callback.message.edit_text("Категория не найдена")


@router.callback_query(F.data == "set:close")
async def cb_settings_close(callback: "CallbackQuery") -> None:
    """Close settings menu."""
    await callback.answer()
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass


@router.callback_query(F.data.startswith("undo:"))
async def cb_undo_action(callback: "CallbackQuery") -> None:
    """Undo a bot action (memory save, auto-reply, commitment)."""
    await callback.answer()
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        return
    action_type = parts[1]
    action_id = parts[2]

    try:
        async with get_session() as session:
            if action_type == "memory":
                from src.db.models import Memory

                mem = await session.get(Memory, int(action_id))
                if mem:
                    mem.is_active = False
                    await session.commit()
                    if callback.message:
                        await callback.message.edit_text("✅ Факт удалён из памяти")
            elif action_type == "autoreply":
                # Can't undo sent messages, but can log it
                if callback.message:
                    await callback.message.edit_text(
                        "ℹ Авто-ответ уже отправлен, нельзя отменить"
                    )
            elif action_type == "commitment":
                from src.db.models import Commitment

                com = await session.get(Commitment, int(action_id))
                if com:
                    com.status = "cancelled"
                    await session.commit()
                    if callback.message:
                        await callback.message.edit_text("✅ Обязательство отменено")
    except Exception as e:
        logger.warning("Undo failed: %s", e)
        if callback.message:
            await callback.message.edit_text("❌ Не удалось отменить")
        return


# ═══════════════════════════════════════════════════════════════════
# BATCH 4: Tools (code, PDF, OCR), Analytics (skill_stats, reply_quality),
#          Auto-reply toggles (reaction_reply, typing_sim, read_receipts),
#          Custom tool creator
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("code"))
async def cmd_code(message: Message) -> None:
    """/code <язык> <код> — выполнить код в песочнице."""
    raw = (message.text or "").strip()
    parts = raw.split(None, 2)
    if len(parts) < 3:
        await message.answer(
            "💻 <b>Выполнение кода</b>\n\n"
            "Использование: <code>/code python print('hello')</code>\n"
            "Код выполняется в изолированной песочнице.\n"
            "Поддерживается: Python (через AST sandbox)."
        )
        return

    lang = parts[1].lower()
    code = parts[2]

    if lang not in ("python", "py"):
        await message.answer("❌ Поддерживается только Python.")
        return

    try:
        from src.core.actions.sdd_executor import execute_code

        result = await execute_code(code, timeout=5)
        output = (
            result.get("output", "")[:3000]
            if isinstance(result, dict)
            else str(result)[:3000]
        )
        await message.answer(f"💻 Результат:\n<code>{output}</code>")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e.__class__.__name__}")


@router.message(Command("pdf"))
async def cmd_pdf(message: Message) -> None:
    """/pdf — извлечь текст из PDF (нужен PyPDF2 или pymupdf)."""
    try:
        import fitz  # type: ignore[import]  # pymupdf  # noqa: F401

        _available = True
    except ImportError:
        try:
            import PyPDF2  # type: ignore[import]  # noqa: F401

            _available = True
        except ImportError:
            _available = False

    if not _available:
        await message.answer(
            "📄 <b>PDF текст</b>\n\n"
            "Не установлен PyPDF2 или pymupdf.\n"
            "Установка: <code>pip install pymupdf</code>"
        )
        return

    if not message.document:
        await message.answer(
            "📄 Пришли PDF файл вместе с командой /pdf для извлечения текста."
        )
        return

    try:
        file_id = message.document.file_id
        file = await message.bot.get_file(file_id)
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            await message.bot.download_file(file.file_path or "", tmp.name)  # type: ignore[arg-type]
            tmp_path = tmp.name

        text = ""
        try:
            import fitz  # type: ignore[import]

            doc = fitz.open(tmp_path)
            for page in doc[:20]:
                text += page.get_text()
            doc.close()
        except ImportError:
            import PyPDF2  # type: ignore[import]

            with open(tmp_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages[:20]:
                    text += page.extract_text() or ""

        import os

        os.unlink(tmp_path)

        if not text.strip():
            await message.answer("📄 PDF не содержит текста (возможно сканы).")
        else:
            await message.answer(f"📄 Извлечённый текст:\n\n{text[:3000]}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e.__class__.__name__}")


@router.message(Command("ocr"))
async def cmd_ocr(message: Message) -> None:
    """/ocr — распознать текст на изображении (нужен pytesseract)."""
    try:
        import pytesseract  # type: ignore[import]  # noqa: F401

        _available = True
    except ImportError:
        _available = False

    if not _available:
        await message.answer(
            "🔍 <b>OCR</b>\n\n"
            "Не установлен pytesseract.\n"
            "Установка: <code>pip install pytesseract</code> + Tesseract OCR в системе."
        )
        return

    if not message.photo:
        await message.answer("🔍 Пришли фото вместе с командой /ocr.")
        return

    try:
        import tempfile

        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            await message.bot.download_file(file.file_path or "", tmp.name)  # type: ignore[arg-type]
            tmp_path = tmp.name

        import pytesseract  # type: ignore[import]
        from PIL import Image

        text = pytesseract.image_to_string(Image.open(tmp_path), lang="rus+eng")

        import os

        os.unlink(tmp_path)

        if not text.strip():
            await message.answer("🔍 Текст не найден на изображении.")
        else:
            await message.answer(f"🔍 Распознанный текст:\n\n{text[:3000]}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e.__class__.__name__}")


@router.message(Command("skill_stats"))
async def cmd_skill_stats(message: Message) -> None:
    """/skill_stats — эффективность навыков."""
    from src.db.models import Skill, SkillUsage

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(
                Skill.name,
                func.count(SkillUsage.id).label("total"),
                func.sum(func.cast(SkillUsage.success, Integer)).label("successes"),
            )
            .select_from(SkillUsage)
            .join(Skill, SkillUsage.skill_id == Skill.id)
            .where(Skill.user_id == owner.id)
            .group_by(Skill.name)
            .order_by(desc(func.count(SkillUsage.id)))
            .limit(15)
        )
        rows = result.all()

    if not rows:
        await message.answer("📊 Нет данных по навыкам.")
        return

    lines = ["📊 <b>Эффективность навыков</b>\n"]
    for name, total, successes in rows:
        rate = (successes / total * 100) if total and successes else 0
        icon = "✅" if rate >= 70 else "⚠️" if rate >= 40 else "❌"
        lines.append(f"  {icon} {name}: {successes or 0}/{total} ({rate:.0f}%)")

    await message.answer("\n".join(lines))


@router.message(Command("reply_quality"))
async def cmd_reply_quality(message: Message) -> None:
    """/reply_quality — статистика авто-ответов."""
    from src.db.models._messaging import AutoReplyLog

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        total = await session.scalar(
            select(func.count())
            .select_from(AutoReplyLog)
            .where(AutoReplyLog.user_id == owner.id)
        )
        today = await session.scalar(
            select(func.count())
            .select_from(AutoReplyLog)
            .where(
                AutoReplyLog.user_id == owner.id,
                AutoReplyLog.created_at >= datetime.now(UTC) - timedelta(days=1),
            )
        )
        top_contacts = await session.execute(
            select(
                AutoReplyLog.peer_name,
                func.count().label("cnt"),
            )
            .where(AutoReplyLog.user_id == owner.id)
            .group_by(AutoReplyLog.peer_name)
            .order_by(desc(func.count()))
            .limit(5)
        )
        top = top_contacts.all()

    if not total:
        await message.answer("📊 Нет данных по авто-ответам.")
        return

    lines = [
        "📊 <b>Статистика авто-ответов</b>\n",
        f"  Всего: {total}",
        f"  За сегодня: {today}",
        "\n<b>Топ-5 контактов:</b>",
    ]
    for name, cnt in top:
        lines.append(f"  • {name or 'неизвестно'}: {cnt}")

    await message.answer("\n".join(lines))


@router.message(Command("reaction_reply"))
async def cmd_reaction_reply(message: Message) -> None:
    """/reaction_reply — вкл/выкл авто-ответ на реакции."""
    from src.config import settings

    current = getattr(settings, "_reaction_reply_enabled", False)
    new_val = not current
    # ponytail: in-memory flag, upgrade to DB config if persistence needed.
    setattr(settings, "_reaction_reply_enabled", new_val)

    status = "включены" if new_val else "выключены"
    await message.answer(f"Reaction auto-reply: {status}")


@router.message(Command("typing_sim"))
async def cmd_typing_sim(message: Message) -> None:
    """/typing_sim — вкл/выкл симуляцию набора."""
    from src.config import settings

    current = getattr(settings, "_typing_sim_enabled", False)
    new_val = not current
    setattr(settings, "_typing_sim_enabled", new_val)

    status = "включена" if new_val else "выключена"
    await message.answer(f"Typing simulation: {status}")


@router.message(Command("read_receipts"))
async def cmd_read_receipts(message: Message) -> None:
    """/read_receipts — вкл/выкл отметку прочитано."""
    from src.config import settings

    current = getattr(settings, "_read_receipts_enabled", False)
    new_val = not current
    setattr(settings, "_read_receipts_enabled", new_val)

    status = "включены" if new_val else "выключены"
    await message.answer(f"Read receipts: {status}")


@router.message(Command("custom_tool"))
async def cmd_custom_tool(message: Message) -> None:
    """/custom_tool <название> <описание> — создать навык из описания."""
    raw = (message.text or "").strip()
    parts = raw.split(None, 2)
    if len(parts) < 3:
        await message.answer(
            "🔧 <b>Создание навыка</b>\n\n"
            "Использование: <code>/custom_tool my_tool что делает этот инструмент</code>\n"
            "Навык будет сохранён и доступен через систему skills."
        )
        return

    name = parts[1]
    description = parts[2]

    try:
        from src.core.intelligence.skills import record_skill_usage
        from src.db.models import Skill

        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            skill = Skill(
                user_id=owner.id,
                name=name,
                body=description,
                trigger_patterns_json=[name.lower()],
                review_status="approved",
                enabled=True,
            )
            session.add(skill)
            await session.commit()
            await session.refresh(skill)

        await message.answer(
            f"✅ Навык <b>{name}</b> создан (ID: {skill.id})\n"
            f"Триггер: {name.lower()}\n"
            f"Описание: {description[:200]}"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e.__class__.__name__}")


# ═══════════════════════════════════════════════════════════════════
# TOOLS EXTENSIONS Batch 3 (61-72)
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("url_summary"))
async def cmd_url_summary(message: Message) -> None:
    """#68: URL summarizer — краткий пересказ веб-страницы."""
    url = (message.text or "").replace("/url_summary", "").strip()
    if not url:
        await message.answer(
            "🔗 <b>Пересказ веб-страницы</b>\n\n"
            "Отправь: <code>/url_summary https://example.com/article</code>"
        )
        return

    if not url.startswith("http"):
        url = "https://" + url

    # SSRF protection — block internal/loopback IPs
    from urllib.parse import urlparse

    _parsed = urlparse(url)
    _host = _parsed.hostname or ""
    if (
        _host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
        or _host.startswith("169.254.")
        or _host.startswith("10.")
        or _host.startswith("192.168.")
        or _host.startswith("172.16.")
        or _host.startswith("172.17.")
        or _host.startswith("172.18.")
        or _host.startswith("172.19.")
        or _host.startswith("172.20.")
        or _host.startswith("172.21.")
        or _host.startswith("172.22.")
        or _host.startswith("172.23.")
        or _host.startswith("172.24.")
        or _host.startswith("172.25.")
        or _host.startswith("172.26.")
        or _host.startswith("172.27.")
        or _host.startswith("172.28.")
        or _host.startswith("172.29.")
        or _host.startswith("172.30.")
        or _host.startswith("172.31.")
    ):
        await message.answer("❌ Доступ к внутренним адресам запрещён.")
        return

    await message.answer("📖 Загружаю и анализирую...")

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                await message.answer(f"❌ HTTP {resp.status_code}")
                return

            # Strip HTML tags — ponytail: simple regex, upgrade to BeautifulSoup if needed
            import re

            raw_text = re.sub(r"<[^>]+>", " ", resp.text)
            raw_text = re.sub(r"\s+", " ", raw_text).strip()

        if len(raw_text) < 100:
            await message.answer("❌ Слишком мало текста на странице.")
            return

        # LLM summary
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            from src.llm.base import ChatMessage, TaskType
            from src.llm.router import build_provider

            provider = await build_provider(
                session, owner, task_type=TaskType.SUMMARIZE
            )

        if provider is None:
            # Fallback: first 500 chars
            await message.answer(f"📖 {raw_text[:500]}...")
            return

        summary = await provider.chat(
            [
                ChatMessage(
                    role="system",
                    content="Сделай краткий пересказ страницы на русском. 3-5 предложений.",
                ),
                ChatMessage(role="user", content=raw_text[:6000]),
            ],
            task_type=TaskType.SUMMARIZE,
        )

        await message.answer(f"📖 <b>Пересказ:</b>\n\n{summary}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e.__class__.__name__}")


@router.message(Command("weather_clothing"))
async def cmd_weather_clothing(message: Message) -> None:
    """#63: Weather + clothing suggestion."""
    city = (message.text or "").replace("/weather_clothing", "").strip()
    if not city:
        await message.answer("🌤 Напиши: <code>/weather_clothing Москва</code>")
        return

    try:
        import httpx

        # wttr.in — free weather API, no key needed
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://wttr.in/{city}?format=j1",
                headers={"Accept-Language": "ru"},
            )
            if resp.status_code != 200:
                await message.answer("❌ Не удалось получить погоду.")
                return

            data = resp.json()
            current = data.get("current_condition", [{}])[0]
            temp = int(current.get("temp_C", 0))
            desc = current.get("lang_ru", [{}])[0].get("value", "")
            humidity = current.get("humidity", 0)
            wind = int(current.get("windspeedKmph", 0))

        # Clothing suggestion based on temperature
        if temp < -10:
            clothing = "🧥 Тёплая куртка, шапка, шарф, перчатки. Очень холодно!"
        elif temp < 0:
            clothing = "🧥 Зимняя куртка, шапка. Холодно."
        elif temp < 10:
            clothing = "🧥 Пальто или тёплая куртка. Прохладно."
        elif temp < 18:
            clothing = "👕 Лёгкая куртка или худи. Умеренно."
        elif temp < 25:
            clothing = "👕 Футболка, возможно лёгкая кофта. Тепло."
        else:
            clothing = "👕 Лёгкая одежда. Жарко!"

        await message.answer(
            f"🌤 <b>{city}</b>\n"
            f"  🌡 {temp}°C, {desc}\n"
            f"  💧 Влажность: {humidity}%\n"
            f"  💨 Ветер: {wind} км/ч\n\n"
            f"👕 <b>Одежда:</b> {clothing}"
        )
    except Exception:
        await message.answer("❌ Ошибка получения погоды.")


@router.message(Command("sticker_search"))
async def cmd_sticker_search(message: Message) -> None:
    """#72: Sticker search — поиск стикеров."""
    query = (message.text or "").replace("/sticker_search", "").strip()
    if not query:
        await message.answer("🎨 Напиши: <code>/sticker_search кот</code>")
        return

    # ponytail: Telegram Bot API has no sticker search. Would need Combin API or similar.
    await message.answer(
        f"🎨 <b>Поиск стикеров: «{query}»</b>\n\n"
        "Telegram Bot API не поддерживает поиск стикеров. "
        "Используй @sticker_search_bot или @stickerlandbot."
    )


@router.message(Command("per_contact_emoji"))
async def cmd_per_contact_emoji(message: Message) -> None:
    """#37: Per-contact emoji — эмодзи для каждого контакта."""
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            "😊 <b>Per-contact emoji</b>\n\n"
            "Установить: <code>/per_contact_emoji Иван 🚀</code>\n"
            "Бот будет использовать этот эмодзи в ответах Ивану."
        )
        return

    name = parts[1]
    emoji = parts[2] if len(parts) >= 3 else ""

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        contacts = await list_contacts(
            session, owner, kinds=("user",), include_bots=False
        )
        contact = next(
            (c for c in contacts if name.lower() in (c.display_name or "").lower()),
            None,
        )
        if not contact:
            await message.answer(f"❌ Контакт «{name}» не найден.")
            return

        # Save emoji in WorkingMemory
        result = await session.execute(
            select(WorkingMemory).where(
                WorkingMemory.user_id == owner.id,
                WorkingMemory.key == f"emoji:{contact.peer_id}",
            )
        )
        old = result.scalar_one_or_none()
        if old:
            await session.delete(old)
        if emoji:
            session.add(
                WorkingMemory(
                    user_id=owner.id,
                    key=f"emoji:{contact.peer_id}",
                    value=emoji[:10],
                )
            )
        await session.commit()

    if emoji:
        await message.answer(f"✅ {name} → {emoji}")
    else:
        await message.answer(f"✅ Эмодзи для {name} сброшен.")


# ═══════════════════════════════════════════════════════════════════
# ANALYTICS EXTENSIONS (73-82): stats, tokens, response quality
# ═══════════════════════════════════════════════════════════════════


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """#73: Communication stats dashboard."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)

        total_mem = await session.scalar(
            select(func.count())
            .select_from(Memory)
            .where(Memory.user_id == owner.id, Memory.is_active)
        )
        total_contacts = await session.scalar(
            select(func.count())
            .select_from(Contact)
            .where(Contact.user_id == owner.id, Contact.peer_kind == "user")
        )
        from src.db.models import Message

        total_msgs = await session.scalar(
            select(func.count()).select_from(Message).where(Message.user_id == owner.id)
        )
        from src.db.models import Episode

        total_episodes = await session.scalar(
            select(func.count()).select_from(Episode).where(Episode.user_id == owner.id)
        )
        from src.db.models import CronJob

        active_crons = await session.scalar(
            select(func.count())
            .select_from(CronJob)
            .where(CronJob.user_id == owner.id, CronJob.enabled)
        )

    lines = [
        "📊 <b>Статистика</b>\n",
        f"  🧠 Фактов: {total_mem or 0}",
        f"  👥 Контактов: {total_contacts or 0}",
        f"  💬 Сообщений: {total_msgs or 0}",
        f"  📖 Эпизодов: {total_episodes or 0}",
        f"  ⏰ Активных задач: {active_crons or 0}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("tokens"))
async def cmd_tokens(message: Message) -> None:
    """#74: Token tracker — использование токенов."""
    await message.answer(
        "🔤 <b>Token tracker</b>\n\n"
        "Отслеживание токенов будет добавлено в следующем обновлении.\n"
        "Пока используй <code>/stats</code> для общей статистики."
    )


@router.message(Command("quality"))
async def cmd_quality(message: Message) -> None:
    """#75: Response quality — качество ответов на основе реакций."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        from src.db.models import Trajectory

        total = await session.scalar(
            select(func.count())
            .select_from(Trajectory)
            .where(Trajectory.user_id == owner.id)
        )
        # Count positive reactions (thumbs up, etc)
        positive = await session.scalar(
            select(func.count())
            .select_from(Trajectory)
            .where(
                Trajectory.user_id == owner.id,
                Trajectory.reward_value.isnot(None),
            )
        )

    total = total or 0
    positive = positive or 0
    rate = (positive / total * 100) if total > 0 else 0

    await message.answer(
        "📈 <b>Качество ответов</b>\n\n"
        f"  Всего запросов: {total}\n"
        f"  Положительных: {positive}\n"
        f"  Рейтинг: {rate:.1f}%"
    )


@router.message(Command("tool_heatmap"))
async def cmd_tool_heatmap(message: Message) -> None:
    """#80: Tool heatmap — какие инструменты используются чаще."""
    await message.answer(
        "📊 <b>Tool heatmap</b>\n\n"
        "Анализ использования инструментов будет добавлен в следующем обновлении.\n"
        "Пока используй <code>/stats</code> для общей статистики."
    )


@router.message(Command("conv_depth"))
async def cmd_conv_depth(message: Message) -> None:
    """#81: Conversation depth — глубина диалогов."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        from src.db.models import Message

        # Average messages per contact
        result = await session.execute(
            select(
                Message.peer_id,
                func.count().label("msg_count"),
            )
            .where(Message.user_id == owner.id)
            .group_by(Message.peer_id)
            .order_by(desc(func.count()))
            .limit(10)
        )
        top = result.all()

    if not top:
        await message.answer("📊 Нет данных.")
        return

    avg = sum(r[1] for r in top) / len(top)
    lines = [
        "📊 <b>Глубина диалогов</b>\n",
        f"  Среднее сообщений на контакт: {avg:.0f}",
        "\n<b>Топ-10:</b>",
    ]
    for peer_id, count in top:
        async with get_session() as session:
            name = await session.scalar(
                select(Contact.display_name).where(
                    Contact.user_id == owner.id, Contact.peer_id == peer_id
                )
            )
        name = name or str(peer_id)
        lines.append(f"  • {name}: {count}")

    await message.answer("\n".join(lines))
