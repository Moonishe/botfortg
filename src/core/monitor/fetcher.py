"""Фетчинг истории сообщений из отслеживаемых источников и проверка правил."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from telethon.errors import FloodWaitError

from src.db.models._monitor import MonitoredSource, MonitorRule, MonitoredMessage
from src.db.session import get_session

logger = logging.getLogger(__name__)

# Период по умолчанию для фетчинга (часы)
DEFAULT_SINCE_HOURS = 24
# Максимальное число сообщений за один заход
MAX_FETCH_LIMIT = 200
# Базовое ожидание при FloodWait
FLOOD_BASE_DELAY = 5.0


async def fetch_history(
    client,  # TelegramClient (lazy import для избежания циркулярных импортов)
    source: MonitoredSource,
    limit: int = 100,
    since_hours: int = DEFAULT_SINCE_HOURS,
) -> list[dict]:
    """Фетчит свежие сообщения из отслеживаемого источника.

    Использует client.iter_messages() с offset_id = source.last_message_id
    для инкрементальной загрузки. Обрабатывает FloodWaitError с экспоненциальным
    бэк-оффом. После успеха обновляет source.last_fetched_at и last_message_id.

    Args:
        client: Активный Telethon-клиент.
        source: ORM-объект MonitoredSource.
        limit: Макс. число сообщений за один заход.
        since_hours: Глубина фетчинга в часах.

    Returns:
        Список словарей сообщений с ключами:
        message_id, date, sender_id, sender_name, text, media_type, entities,
        views, forwards.
    """
    offset_id = source.last_message_id or 0
    since_date = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    messages: list[dict] = []
    max_retries = 3

    for attempt in range(max_retries):
        try:
            # Lazy import Telethon типов
            from telethon.tl.types import Message as TgMessage

            async for msg in client.iter_messages(
                source.entity_id,
                limit=min(limit, MAX_FETCH_LIMIT),
                offset_id=offset_id if offset_id > 0 else 0,
                reverse=False,
            ):
                if not isinstance(msg, TgMessage):
                    continue

                # Пропускаем сообщения старше since_hours
                if msg.date and msg.date < since_date:
                    continue

                # Извлекаем текст
                text = msg.message or ""
                caption = getattr(msg, "caption", None)
                if not text and caption:
                    text = caption

                # Определяем тип медиа
                media_type = None
                if msg.media:
                    media_type = (
                        type(msg.media).__name__.replace("MessageMedia", "").lower()
                    )
                    if media_type == "empty":
                        media_type = None

                # Сериализуем entities если есть
                entities_serialized = None
                if msg.entities:
                    entities_serialized = [
                        {
                            "type": type(e).__name__,
                            "offset": e.offset,
                            "length": e.length,
                            "url": getattr(e, "url", None),
                        }
                        for e in msg.entities
                    ]

                sender_raw = getattr(msg, "sender", None)
                sender_name = None
                if sender_raw is not None:
                    sender_name = getattr(sender_raw, "first_name", None) or getattr(
                        sender_raw, "title", None
                    )

                messages.append(
                    {
                        "message_id": msg.id,
                        "date": msg.date,
                        "sender_id": getattr(msg, "sender_id", None),
                        "sender_name": sender_name,
                        "text": text or None,
                        "media_type": media_type,
                        "entities": entities_serialized,
                        "views": getattr(msg, "views", None),
                        "forwards": getattr(msg, "forwards", None),
                    }
                )

            break  # Успех — выходим из цикла ретраев

        except FloodWaitError as e:
            wait = e.seconds
            if wait > 30:
                logger.error(
                    "FloodWait too long (%ds) for source %s, aborting",
                    wait,
                    source.title,
                )
                raise RuntimeError(
                    f"FloodWait {wait}с для {source.title}: "
                    f"сервер просит подождать слишком долго, фетчинг прерван."
                ) from e
            logger.warning(
                "FloodWait %ds for source %s (attempt %d/%d)",
                wait,
                source.title,
                attempt + 1,
                max_retries,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(wait)
                continue
            raise

        except Exception as e:
            logger.exception(
                "fetch_history failed for source %s (entity_id=%d)",
                source.title,
                source.entity_id,
            )
            if attempt < max_retries - 1:
                delay = FLOOD_BASE_DELAY * (2**attempt)
                await asyncio.sleep(delay)
                continue
            raise

    # Обновляем last_fetched_at и last_message_id если были сообщения
    if messages:
        newest_id = max(m["message_id"] for m in messages)
        async with get_session() as session:
            await session.execute(
                update(MonitoredSource)
                .where(MonitoredSource.id == source.id)
                .values(
                    last_fetched_at=datetime.now(timezone.utc),
                    last_message_id=max(newest_id, source.last_message_id),
                )
            )
            # Обновляем in-memory объект
            source.last_fetched_at = datetime.now(timezone.utc)
            source.last_message_id = max(newest_id, source.last_message_id)

    return messages


def match_rules(message_dict: dict, rules: list[MonitorRule]) -> list[MonitorRule]:
    """Проверяет, какие правила срабатывают на сообщение.

    Поддерживает:
    - keywords: список ключевых слов (регистронезависимый поиск в тексте)
    - exclude_keywords: список слов-исключений
    - regex: регулярное выражение для текста

    Args:
        message_dict: Словарь сообщения из fetch_history().
        rules: Список ORM-объектов MonitorRule.

    Returns:
        Список сработавших правил (может быть пустым).
    """
    text = (message_dict.get("text") or "").lower()
    if not text:
        return []

    matched: list[MonitorRule] = []
    for rule in rules:
        if not rule.is_active:
            continue

        conditions = rule.conditions or {}

        # Проверка exclude_keywords — если есть любое слово-исключение, правило не срабатывает
        exclude_kw = conditions.get("exclude_keywords", [])
        if exclude_kw:
            if any(kw.lower() in text for kw in exclude_kw):
                continue

        # Проверка regex
        regex_pattern = conditions.get("regex")
        if regex_pattern:
            try:
                if not re.search(regex_pattern, text, re.IGNORECASE):
                    continue
            except re.error:
                logger.warning("Invalid regex in rule %d: %s", rule.id, regex_pattern)
                continue

        # Проверка keywords
        keywords = conditions.get("keywords", [])
        if keywords:
            if not any(kw.lower() in text for kw in keywords):
                continue

        matched.append(rule)

    # Сортируем по приоритету (высший первый)
    matched.sort(key=lambda r: r.priority or 0, reverse=True)
    return matched


async def check_periodic(user_id: int) -> list[dict]:
    """Фоновая задача: фетчит историю для всех активных источников пользователя.

    Args:
        user_id: Telegram user_id владельца.

    Returns:
        Список словарей с ключами: source, messages, matched_rules.
    """
    async with get_session() as session:
        stmt = select(MonitoredSource).where(
            MonitoredSource.user_id == user_id,
            MonitoredSource.is_active == True,
        )
        result = await session.execute(stmt)
        sources = result.scalars().all()

    if not sources:
        return []

    # Lazy import — избегаем циркулярного импорта с userbot
    from src.config import settings
    from src.userbot.manager import _MANAGER_SINGLETON

    client = _MANAGER_SINGLETON.get_client(user_id) if _MANAGER_SINGLETON else None
    if client is None:
        logger.warning("check_periodic: no Telethon client for user %d", user_id)
        return []

    results: list[dict] = []

    for source in sources:
        try:
            # Фетчим историю
            msgs = await fetch_history(client, source, limit=100)

            if not msgs:
                continue

            # Загружаем правила для источника
            async with get_session() as session:
                rules_stmt = select(MonitorRule).where(
                    MonitorRule.source_id == source.id,
                    MonitorRule.is_active == True,
                )
                rules_result = await session.execute(rules_stmt)
                rules = list(rules_result.scalars().all())

            # Применяем правила к каждому сообщению
            matched_pairs: list[tuple[dict, list[MonitorRule]]] = []
            for msg_dict in msgs:
                matched = match_rules(msg_dict, rules)
                if matched:
                    matched_pairs.append((msg_dict, matched))

            if matched_pairs:
                results.append(
                    {
                        "source": source,
                        "messages": matched_pairs,
                    }
                )

        except Exception:
            logger.exception(
                "check_periodic failed for source %s (user %d)",
                source.title,
                user_id,
            )
            continue

    return results
