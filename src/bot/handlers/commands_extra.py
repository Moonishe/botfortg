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
from aiogram.types import Message
from sqlalchemy import select, func, desc

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
