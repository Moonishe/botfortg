"""/monitor — управление мониторингом Telegram-каналов и групп."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from src.bot.filters import OwnerOnly
from src.config import settings
from src.core.infra.text_sanitizer import sanitize_html
from src.db.models._monitor import (
    MonitoredAlert,
    MonitoredMessage,
    MonitoredSource,
    MonitorRule,
)
from src.db.repo import get_or_create_user
from src.db.session import get_session

logger = logging.getLogger(__name__)

router = Router(name="monitor_cmd")
router.message.filter(OwnerOnly())


@router.message(Command("monitor"))
async def cmd_monitor(message: Message, command: CommandObject) -> None:
    """Главная команда управления мониторингом каналов."""
    args = (command.args or "").strip().split()
    if not args:
        await message.answer(
            "📡 <b>Мониторинг каналов</b>\n\n"
            "/monitor add @channel — добавить канал\n"
            "/monitor add https://t.me/channel — по ссылке\n"
            "/monitor list — список источников\n"
            "/monitor fetch &lt;id&gt; [hours=24] — ручной запуск\n"
            "/monitor remove &lt;id&gt; — удалить\n"
            "/monitor rules &lt;source_id&gt; — правила для источника\n"
            "/monitor rule_add &lt;source_id&gt; &lt;название&gt; | &lt;ключевые_слова&gt; — добавить правило\n"
            "  ⚠️ Используй | между названием и ключевыми словами\n"
            "/monitor rule_del &lt;rule_id&gt; — удалить правило\n"
            "/monitor status — статус мониторинга"
        )
        return

    action = args[0].lower()

    if action == "add":
        await _handle_add(message, args[1:])
    elif action == "list":
        await _handle_list(message)
    elif action == "fetch":
        await _handle_fetch(message, args[1:])
    elif action == "remove":
        await _handle_remove(message, args[1:])
    elif action == "status":
        await _handle_status(message)
    elif action == "rules":
        await _handle_rules(message, args[1:])
    elif action == "rule_add":
        await _handle_rule_add(message, args[1:])
    elif action == "rule_del":
        await _handle_rule_del(message, args[1:])
    else:
        await message.answer(
            f"❓ Неизвестное действие: <b>{sanitize_html(action)}</b>\nЖми /monitor для справки."
        )


# ═══════════════════════════════════════════════════════════════════════════
#  /monitor add <идентификатор>
# ═══════════════════════════════════════════════════════════════════════════


async def _handle_add(message: Message, args: list[str]) -> None:
    """Добавляет источник мониторинга."""
    if not args:
        await message.answer(
            "Использование: <code>/monitor add @username</code> или "
            "<code>/monitor add https://t.me/channel</code>"
        )
        return

    identifier = " ".join(args)

    # Получаем Telethon-клиент
    from src.userbot.manager import _MANAGER_SINGLETON

    client = (
        _MANAGER_SINGLETON.get_client(settings.owner_telegram_id)
        if _MANAGER_SINGLETON
        else None
    )
    if client is None:
        await message.answer("❌ Юзербот не подключён. Сделай /login.")
        return

    status_msg = await message.answer(
        f"🔍 Разрешаю <code>{sanitize_html(identifier)}</code>…"
    )

    try:
        from src.core.monitor.source_resolver import resolve_source

        info = await resolve_source(client, identifier)
    except ValueError as e:
        await status_msg.edit_text(f"❌ {e}")
        return
    except Exception:
        logger.exception("resolve_source failed for %s", identifier)
        await status_msg.edit_text(
            "❌ Ошибка при разрешении источника. Попробуй позже."
        )
        return

    # Проверяем, нет ли уже такого источника
    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)
        stmt = select(MonitoredSource).where(
            MonitoredSource.user_id == owner.id,
            MonitoredSource.entity_id == info["entity_id"],
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()

        if existing:
            status_line = "✅ Активен" if existing.is_active else "⏸ Неактивен"
            await status_msg.edit_text(
                f"ℹ️ Источник уже в мониторинге:\n\n"
                f"📌 <b>{sanitize_html(existing.title)}</b>\n"
                f"🆔 ID: <code>{existing.id}</code>\n"
                f"📊 Статус: {status_line}\n"
                f"📅 Добавлен: {existing.added_at.strftime('%d.%m.%Y %H:%M') if existing.added_at else '?'}\n\n"
                f"Используй /monitor list для просмотра всех."
            )
            return

        # Сохраняем источник
        try:
            source = MonitoredSource(
                user_id=owner.id,
                entity_id=info["entity_id"],
                entity_type=info["type"],
                title=info["title"],
                username=info.get("username"),
                access_hash=info.get("access_hash"),
                is_active=True,
                settings={"keywords": [], "exclude_keywords": []},
            )
            session.add(source)
            await session.flush()
        except IntegrityError:
            await status_msg.edit_text("ℹ️ Этот источник уже добавлен.")
            return

        # Создаём правило по умолчанию (отслеживать всё)
        default_rule = MonitorRule(
            user_id=owner.id,
            source_id=source.id,
            name="По умолчанию",
            priority=0,
            conditions={"keywords": [], "exclude_keywords": []},
            actions={"notify": True, "save": True, "llm_summary": True},
            is_active=True,
        )
        session.add(default_rule)

        source_id = source.id

    type_emoji = {"channel": "📢", "supergroup": "👥", "group": "👤", "chat": "💬"}.get(
        info["type"], "📡"
    )

    await status_msg.edit_text(
        f"{type_emoji} <b>Источник добавлен!</b>\n\n"
        f"📌 <b>{sanitize_html(info['title'])}</b>\n"
        f"🔗 @{sanitize_html(info['username'] if info.get('username') else '—')}\n"
        f"📂 Тип: {sanitize_html(info['type'])}\n"
        f"🆔 ID источника: <code>{source_id}</code>\n\n"
        f"Создано правило по умолчанию. Используй:\n"
        f"• /monitor rules {source_id} — просмотр правил\n"
        f"• /monitor fetch {source_id} — ручной фетчинг\n"
        f"• /monitor list — список источников"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  /monitor list
# ═══════════════════════════════════════════════════════════════════════════


async def _handle_list(message: Message) -> None:
    """Показывает список всех источников мониторинга."""
    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)
        stmt = (
            select(MonitoredSource)
            .where(MonitoredSource.user_id == owner.id)
            .order_by(MonitoredSource.added_at.desc())
        )
        sources = (await session.execute(stmt)).scalars().all()

    if not sources:
        await message.answer(
            "📡 Список мониторинга пуст.\nДобавь источник: /monitor add @channel"
        )
        return

    lines = ["📡 <b>Мониторинг каналов</b>\n"]
    type_emojis = {"channel": "📢", "supergroup": "👥", "group": "👤", "chat": "💬"}

    for src in sources:
        emoji = type_emojis.get(src.entity_type, "📡")
        status = "✅" if src.is_active else "⏸"
        last_fetch = (
            src.last_fetched_at.strftime("%d.%m.%Y %H:%M")
            if src.last_fetched_at
            else "никогда"
        )
        lines.append(
            f"{emoji} <b>{sanitize_html(src.title or 'Без названия')}</b>\n"
            f"  🆔 ID: <code>{src.id}</code> | Статус: {status}\n"
            f"  📥 Последний фетч: {last_fetch}\n"
            f"  💬 Последнее сообщение: #{src.last_message_id or 0}"
        )

    text = "\n".join(lines)
    await message.answer(text)


# ═══════════════════════════════════════════════════════════════════════════
#  /monitor fetch <source_id> [hours=24]
# ═══════════════════════════════════════════════════════════════════════════


async def _handle_fetch(message: Message, args: list[str]) -> None:
    """Ручной запуск фетчинга сообщений из источника."""
    if not args:
        await message.answer(
            "Использование: <code>/monitor fetch &lt;source_id&gt; [hours=24]</code>\n"
            "Пример: <code>/monitor fetch 1 48</code>"
        )
        return

    try:
        source_id = int(args[0])
    except ValueError:
        await message.answer("❌ source_id должен быть числом.")
        return

    hours = 24
    if len(args) >= 2:
        try:
            hours = int(args[1])
        except ValueError:
            await message.answer("❌ Часы должны быть числом.")
            return
        hours = max(1, min(hours, 168))  # 1 час – 7 дней

    # Загружаем источник из БД
    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)
        stmt = select(MonitoredSource).where(
            MonitoredSource.id == source_id,
            MonitoredSource.user_id == owner.id,
        )
        source = (await session.execute(stmt)).scalar_one_or_none()

        if source is None:
            await message.answer(f"❌ Источник #{source_id} не найден.")
            return

        # Загружаем правила
        rules_stmt = select(MonitorRule).where(
            MonitorRule.source_id == source.id,
            MonitorRule.is_active == True,
        )
        rules = list((await session.execute(rules_stmt)).scalars().all())

    # Получаем клиент
    from src.userbot.manager import _MANAGER_SINGLETON

    client = (
        _MANAGER_SINGLETON.get_client(settings.owner_telegram_id)
        if _MANAGER_SINGLETON
        else None
    )
    if client is None:
        await message.answer("❌ Юзербот не подключён. Сделай /login.")
        return

    status_msg = await message.answer(
        f"📥 Фетчу сообщения из <b>{sanitize_html(source.title)}</b> за <b>{hours}ч</b>…"
    )

    try:
        from src.core.monitor.fetcher import fetch_history, match_rules

        msgs = await fetch_history(client, source, limit=100, since_hours=hours)

        if not msgs:
            await status_msg.edit_text(
                f"📭 Новых сообщений в <b>{sanitize_html(source.title)}</b> не найдено (за {hours}ч)."
            )
            return

        # Применяем правила
        matched_pairs: list[tuple[dict, list[MonitorRule]]] = []
        for msg_dict in msgs:
            matched = match_rules(msg_dict, rules)
            if matched:
                matched_pairs.append((msg_dict, matched))

        if not matched_pairs:
            await status_msg.edit_text(
                f"📥 Получено <b>{len(msgs)}</b> сообщений, "
                f"но ни одно правило не сработало.\n"
                f"Проверь правила: /monitor rules {source.id}"
            )
            return

        # Сохраняем сообщения и алерты
        alerts_created = 0
        async with get_session() as session:
            # Обновляем last_fetched_at для источника
            source = await session.merge(source)
            source.last_fetched_at = datetime.now(timezone.utc)

            for msg_dict, matched_rules in matched_pairs:
                # Сохраняем сообщение (пропускаем, если уже есть — concurrent fetch)
                try:
                    async with session.begin_nested():
                        db_msg = MonitoredMessage(
                            source_id=source.id,
                            message_id=msg_dict["message_id"],
                            date=msg_dict["date"],
                            sender_id=msg_dict.get("sender_id"),
                            sender_name=msg_dict.get("sender_name"),
                            text=msg_dict.get("text"),
                            media_type=msg_dict.get("media_type"),
                            entities=msg_dict.get("entities"),
                            views=msg_dict.get("views"),
                            forwards=msg_dict.get("forwards"),
                        )
                        session.add(db_msg)
                        await session.flush()
                except IntegrityError:
                    continue  # savepoint rolled back, skip duplicate

                # Создаём алерты
                for rule in matched_rules:
                    alert = MonitoredAlert(
                        user_id=owner.id,
                        rule_id=rule.id,
                        message_id=db_msg.id,
                        status="pending",
                    )
                    session.add(alert)
                    alerts_created += 1

        await status_msg.edit_text(
            f"📥 <b>Фетч завершён: {sanitize_html(source.title)}</b>\n\n"
            f"📨 Сообщений: <b>{len(msgs)}</b>\n"
            f"🎯 Сработавших правил: <b>{len(matched_pairs)}</b>\n"
            f"🚨 Алертов создано: <b>{alerts_created}</b>\n\n"
            f"Используй /monitor list для просмотра."
        )

    except Exception:
        logger.exception("fetch failed for source %s", source.title)
        await status_msg.edit_text(
            f"❌ Ошибка фетчинга <b>{sanitize_html(source.title)}</b>. Проверь права доступа и логи."
        )


# ═══════════════════════════════════════════════════════════════════════════
#  /monitor remove <source_id>
# ═══════════════════════════════════════════════════════════════════════════


async def _handle_remove(message: Message, args: list[str]) -> None:
    """Удаляет источник мониторинга."""
    if not args:
        await message.answer(
            "Использование: <code>/monitor remove &lt;source_id&gt;</code>"
        )
        return

    try:
        source_id = int(args[0])
    except ValueError:
        await message.answer("❌ source_id должен быть числом.")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)

        # Удаляем orphan-записи ПЕРЕД удалением источника
        # 1. Алерты для сообщений этого источника
        await session.execute(
            delete(MonitoredAlert).where(
                MonitoredAlert.message_id.in_(
                    select(MonitoredMessage.id).where(
                        MonitoredMessage.source_id == source_id
                    )
                )
            )
        )
        # 2. Сообщения этого источника
        await session.execute(
            delete(MonitoredMessage).where(MonitoredMessage.source_id == source_id)
        )
        # 3. Правила этого источника
        await session.execute(
            delete(MonitorRule).where(MonitorRule.source_id == source_id)
        )
        # 4. Сам источник
        stmt = delete(MonitoredSource).where(
            MonitoredSource.id == source_id,
            MonitoredSource.user_id == owner.id,
        )
        result = await session.execute(stmt)
        await session.commit()

    if result.rowcount:
        await message.answer(f"🗑 Источник #{source_id} и все связанные данные удалены.")
    else:
        await message.answer(f"❌ Источник #{source_id} не найден.")


# ═══════════════════════════════════════════════════════════════════════════
#  /monitor status
# ═══════════════════════════════════════════════════════════════════════════


async def _handle_status(message: Message) -> None:
    """Показывает статус мониторинга."""
    # Проверяем юзербот
    from src.userbot.manager import _MANAGER_SINGLETON

    client = (
        _MANAGER_SINGLETON.get_client(settings.owner_telegram_id)
        if _MANAGER_SINGLETON
        else None
    )
    userbot_ok = client is not None and client.is_connected()

    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)

        # Считаем источники
        src_count = (
            (
                await session.execute(
                    select(MonitoredSource).where(
                        MonitoredSource.user_id == owner.id,
                        MonitoredSource.is_active == True,
                    )
                )
            )
            .scalars()
            .all()
        )
        total_sources = len(list(src_count))

        # Считаем правила
        rules_count = (
            (
                await session.execute(
                    select(MonitorRule).where(
                        MonitorRule.user_id == owner.id,
                        MonitorRule.is_active == True,
                    )
                )
            )
            .scalars()
            .all()
        )
        total_rules = len(list(rules_count))

        # Считаем алерты
        pending_alerts = (
            (
                await session.execute(
                    select(MonitoredAlert).where(
                        MonitoredAlert.user_id == owner.id,
                        MonitoredAlert.status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )
        pending_count = len(list(pending_alerts))

    await message.answer(
        "📡 <b>Статус мониторинга</b>\n\n"
        f"🤖 Юзербот: {'✅ Подключён' if userbot_ok else '❌ Не подключён'}\n"
        f"📌 Активных источников: <b>{total_sources}</b>\n"
        f"📋 Активных правил: <b>{total_rules}</b>\n"
        f"🚨 Ожидающих алертов: <b>{pending_count}</b>\n\n"
        f"Команды: /monitor list | /monitor add | /monitor fetch"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  /monitor rules <source_id>
# ═══════════════════════════════════════════════════════════════════════════


async def _handle_rules(message: Message, args: list[str]) -> None:
    """Показывает правила для источника."""
    if not args:
        await message.answer(
            "Использование: <code>/monitor rules &lt;source_id&gt;</code>"
        )
        return

    try:
        source_id = int(args[0])
    except ValueError:
        await message.answer("❌ source_id должен быть числом.")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)

        # Проверяем источник
        src = (
            await session.execute(
                select(MonitoredSource).where(
                    MonitoredSource.id == source_id,
                    MonitoredSource.user_id == owner.id,
                )
            )
        ).scalar_one_or_none()

        if src is None:
            await message.answer(f"❌ Источник #{source_id} не найден.")
            return

        rules = list(
            (
                await session.execute(
                    select(MonitorRule)
                    .where(MonitorRule.source_id == source_id)
                    .order_by(MonitorRule.priority.desc())
                )
            )
            .scalars()
            .all()
        )

    if not rules:
        await message.answer(
            f"📋 Для <b>{sanitize_html(src.title)}</b> нет правил.\n"
            f"Добавь: <code>/monitor rule_add {source_id} Название ключевые_слова</code>"
        )
        return

    lines = [f"📋 <b>Правила для {sanitize_html(src.title)}</b> (ID: {source_id})\n"]
    for rule in rules:
        status = "✅" if rule.is_active else "⏸"
        conds = rule.conditions or {}
        keywords = conds.get("keywords", [])
        exclude = conds.get("exclude_keywords", [])
        regex = conds.get("regex")
        kw_str = ", ".join(keywords) if keywords else "все"
        ex_str = f" (кроме: {', '.join(exclude)})" if exclude else ""
        rx_str = f" [regex: {regex}]" if regex else ""

        lines.append(
            f"• <b>{sanitize_html(rule.name or 'Без названия')}</b> ({status})\n"
            f"  🆔 ID правила: <code>{rule.id}</code> | Приоритет: {rule.priority}\n"
            f"  🔑 Ключевые слова: {sanitize_html(kw_str)}{sanitize_html(ex_str)}{sanitize_html(rx_str)}"
        )

    text = "\n".join(lines)
    await message.answer(text)


# ═══════════════════════════════════════════════════════════════════════════
#  /monitor rule_add <source_id> <название> | <ключевые_слова через запятую>
# ═══════════════════════════════════════════════════════════════════════════


async def _handle_rule_add(message: Message, args: list[str]) -> None:
    """Добавляет правило фильтрации для источника."""
    if len(args) < 2:
        await message.answer(
            "Использование: <code>/monitor rule_add &lt;source_id&gt; "
            "&lt;название&gt; | &lt;ключевые_слова&gt;</code>\n"
            "Пример: <code>/monitor rule_add 1 Важное | срочно,важно,деньги</code>"
        )
        return

    try:
        source_id = int(args[0])
    except ValueError:
        await message.answer("❌ source_id должен быть числом.")
        return

    # Проверяем разделитель | для многословных названий
    remaining = args[1:]
    if "|" in remaining:
        # Разделяем название и ключевые слова по |
        pipe_idx = remaining.index("|")
        name = " ".join(remaining[:pipe_idx]).strip()
        keywords_str = (
            " ".join(remaining[pipe_idx + 1 :]).strip()
            if pipe_idx + 1 < len(remaining)
            else ""
        )
    elif len(remaining) >= 2:
        # Больше одного токена без разделителя — неоднозначность
        await message.answer(
            "⚠️ Для многословного названия используй разделитель <code>|</code>:\n"
            "<code>/monitor rule_add &lt;source_id&gt; &lt;название&gt; | &lt;ключевые_слова&gt;</code>\n"
            "Пример: <code>/monitor rule_add 1 Мой Важный Фильтр | срочно,важно,деньги</code>"
        )
        return
    else:
        name = remaining[0]
        keywords_str = ""

    keywords = (
        [kw.strip() for kw in keywords_str.split(",") if kw.strip()]
        if keywords_str
        else []
    )

    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)

        # Проверяем источник
        src = (
            await session.execute(
                select(MonitoredSource).where(
                    MonitoredSource.id == source_id,
                    MonitoredSource.user_id == owner.id,
                )
            )
        ).scalar_one_or_none()

        if src is None:
            await message.answer(f"❌ Источник #{source_id} не найден.")
            return

        # Валидация regex в conditions (если задан)
        conditions = {
            "keywords": keywords,
            "exclude_keywords": [],
        }

        rule = MonitorRule(
            user_id=owner.id,
            source_id=source_id,
            name=name,
            priority=0,
            conditions=conditions,
            actions={"notify": True, "save": True, "llm_summary": True},
            is_active=True,
        )
        session.add(rule)
        await session.flush()
        rule_id = rule.id

    kw_display = sanitize_html(", ".join(keywords)) if keywords else "все сообщения"
    await message.answer(
        f"✅ <b>Правило добавлено!</b>\n\n"
        f"📌 Название: <b>{sanitize_html(name)}</b>\n"
        f"🆔 ID правила: <code>{rule_id}</code>\n"
        f"📂 Источник: {sanitize_html(src.title)} (#{source_id})\n"
        f"🔑 Ключевые слова: {kw_display}\n\n"
        f"Используй /monitor rules {source_id} для просмотра."
    )


# ═══════════════════════════════════════════════════════════════════════════
#  /monitor rule_del <rule_id>
# ═══════════════════════════════════════════════════════════════════════════


async def _handle_rule_del(message: Message, args: list[str]) -> None:
    """Удаляет правило фильтрации."""
    if not args:
        await message.answer(
            "Использование: <code>/monitor rule_del &lt;rule_id&gt;</code>"
        )
        return

    try:
        rule_id = int(args[0])
    except ValueError:
        await message.answer("❌ rule_id должен быть числом.")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)
        stmt = delete(MonitorRule).where(
            MonitorRule.id == rule_id,
            MonitorRule.user_id == owner.id,
        )
        result = await session.execute(stmt)

    if result.rowcount:
        await message.answer(f"🗑 Правило #{rule_id} удалено.")
    else:
        await message.answer(f"❌ Правило #{rule_id} не найдено.")
