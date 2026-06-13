"""Отслеживание настроения контактов по реакциям в message_reactions.

Анализирует реакции контактов (👍, ❤️, 👎, 😢 и др.) за указанный период
и вычисляет тренд настроения, дневную динамику и предупреждения о смене.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, UTC

from sqlalchemy import select

from src.db.models import Contact, MessageReaction
from src.db.repo import get_or_create_user
from src.db.session import get_session

logger = logging.getLogger(__name__)

# ── Карта эмодзи → вес настроения ───────────────────────────────────────
_REACTION_MOOD_MAP: dict[str, int] = {
    "\U0001f44d": 1,  # 👍
    "\u2764\ufe0f": 2,  # ❤️
    "\U0001f525": 1,  # 🔥
    "\U0001f44f": 1,  # 👏
    "\U0001f4af": 2,  # 💯
    "\U0001f601": 1,  # 😁
    "\U0001f389": 1,  # 🎉
    "\U0001f44e": -1,  # 👎
    "\U0001f622": -2,  # 😢
    "\U0001f494": -2,  # 💔
    "\U0001f914": 0,  # 🤔
    "\U0001f928": 0,  # 🤨
    "\U0001f631": -1,  # 😱
}

# Эмодзи, считающиеся позитивными (для подсчёта positive_count)
_POSITIVE_EMOJIS: set[str] = {e for e, w in _REACTION_MOOD_MAP.items() if w > 0}

# Эмодзи, считающиеся негативными (для подсчёта negative_count)
_NEGATIVE_EMOJIS: set[str] = {e for e, w in _REACTION_MOOD_MAP.items() if w < 0}

# Эмодзи, считающиеся «растерянными» (для подсчёта confused_count)
_CONFUSED_EMOJIS: set[str] = {
    "\U0001f914",  # 🤔
    "\U0001f928",  # 🤨
}


async def get_contact_mood(
    contact_id: int,
    owner_telegram_id: int,
    days: int = 7,
) -> dict:
    """Анализ настроения контакта по реакциям за N дней.

    Анализирует реакции в таблице message_reactions, поставленные
    контактом (reactor_id) в чате с владельцем.  Возвращает сводку:
    количество позитивных/негативных/растерянных реакций, дневную
    динамику и тренд настроения.

    Args:
        contact_id: peer_id контакта (BigInteger).
        owner_telegram_id: Telegram user_id владельца.
        days: Глубина анализа в днях (по умолчанию 7).

    Returns:
        Словарь с ключами:
          - contact_name: имя контакта (str)
          - positive_count: количество позитивных реакций
          - negative_count: количество негативных реакций
          - confused_count: количество «растерянных» реакций
          - trend: "improving" / "stable" / "declining"
          - daily: список {"date": "...", "positive": N, "negative": N}
          - alert: предупреждение или None
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)

        # Получаем имя контакта
        contact_result = await session.execute(
            select(Contact.display_name).where(
                Contact.user_id == owner.id,
                Contact.peer_id == contact_id,
            )
        )
        contact_row = contact_result.scalar_one_or_none()
        contact_name = contact_row if contact_row else f"ID:{contact_id}"

        # Загружаем реакции контакта за период.
        # Фильтруем по chat_id = contact_id (чат с этим контактом)
        # и исключаем реакции самого владельца (reactor_id != owner.telegram_id).
        reactions_result = await session.execute(
            select(
                MessageReaction.reaction,
                MessageReaction.created_at,
            ).where(
                MessageReaction.user_id == owner.id,
                MessageReaction.chat_id == contact_id,
                MessageReaction.created_at >= cutoff,
            )
        )
        rows = reactions_result.all()

    if not rows:
        return {
            "contact_name": contact_name,
            "positive_count": 0,
            "negative_count": 0,
            "confused_count": 0,
            "trend": "stable",
            "daily": [],
            "alert": None,
        }

    # ── Группировка по дням ──────────────────────────────────────────
    daily_buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"positive": 0, "negative": 0, "confused": 0}
    )
    positive_count = 0
    negative_count = 0
    confused_count = 0

    for reaction, created_at in rows:
        date_key = created_at.strftime("%Y-%m-%d")
        emoji = reaction or ""

        if emoji in _POSITIVE_EMOJIS:
            daily_buckets[date_key]["positive"] += 1
            positive_count += 1
        elif emoji in _NEGATIVE_EMOJIS:
            daily_buckets[date_key]["negative"] += 1
            negative_count += 1
        elif emoji in _CONFUSED_EMOJIS:
            daily_buckets[date_key]["confused"] += 1
            confused_count += 1

    # ── Сортируем дни от новых к старым ──────────────────────────────
    daily_sorted = sorted(
        (
            {
                "date": d,
                "positive": int(v["positive"]),
                "negative": int(v["negative"]),
            }
            for d, v in daily_buckets.items()
        ),
        key=lambda x: x["date"],
        reverse=True,
    )
    daily: list[dict] = daily_sorted

    # ── Определяем тренд: сравниваем первые 3 дня с последними ───────
    trend: str = "stable"
    alert: str | None = None

    def _day_net(day: dict) -> int:
        return int(day.get("positive", 0) or 0) - int(day.get("negative", 0) or 0)

    if len(daily) >= 3:
        # Последние 3 дня (самые новые — начало списка)
        recent = daily[:3]
        # Предыдущие дни (если есть)
        older = daily[3:] if len(daily) > 3 else []

        recent_net = sum(_day_net(d) for d in recent)
        older_net = sum(_day_net(d) for d in older) if older else 0

        # Нормируем на количество дней
        recent_avg = recent_net / len(recent)
        older_avg = older_net / len(older) if older else recent_avg

        if recent_avg > older_avg + 0.5:
            trend = "improving"
        elif recent_avg < older_avg - 0.5:
            trend = "declining"
        else:
            trend = "stable"

    # ── Предупреждение: падение третий день подряд ───────────────────
    if len(daily) >= 3 and trend == "declining":
        # Проверяем, ухудшается ли каждый из последних 3 дней
        last_three_nets = [_day_net(d) for d in daily[:3]]
        if (
            last_three_nets[0] <= last_three_nets[1]
            and last_three_nets[1] <= last_three_nets[2]
        ):
            alert = (
                f"Настроение контакта «{contact_name}» ухудшается третий день подряд"
            )
    elif trend == "declining":
        alert = f"Настроение контакта «{contact_name}» ухудшилось за последние дни"

    return {
        "contact_name": contact_name,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "confused_count": confused_count,
        "trend": trend,
        "daily": daily,
        "alert": alert,
    }


async def detect_mood_shift(
    contact_id: int,
    owner_telegram_id: int,
) -> str | None:
    """Обнаружить резкую смену настроения контакта.

    Сравнивает последние 3 дня с предыдущими 4 днями.
    Возвращает предупреждение (str) или None если сдвиг не обнаружен.

    Args:
        contact_id: peer_id контакта.
        owner_telegram_id: Telegram user_id владельца.

    Returns:
        Текст предупреждения или None.
    """
    mood = await get_contact_mood(
        contact_id=contact_id,
        owner_telegram_id=owner_telegram_id,
        days=7,
    )
    return mood.get("alert")


async def check_mood_alerts(owner_telegram_id: int) -> list[str]:
    """Проверить все контакты владельца на резкую смену настроения.

    Проходит по всем контактам, вызывает detect_mood_shift
    и возвращает список предупреждений.  Вызывается раз в день
    в dream_cycle.

    Args:
        owner_telegram_id: Telegram user_id владельца.

    Returns:
        Список строк-предупреждений (пустой список если всё в порядке).
    """
    alerts: list[str] = []

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)

        # Получаем список peer_id всех контактов пользователя (только люди)
        contacts_result = await session.execute(
            select(Contact.peer_id, Contact.display_name).where(
                Contact.user_id == owner.id,
                Contact.peer_kind == "user",
                Contact.is_bot == False,
            )
        )
        contacts = contacts_result.all()

    if not contacts:
        logger.debug("check_mood_alerts: нет контактов для user=%d", owner_telegram_id)
        return alerts

    for peer_id, display_name in contacts:
        try:
            alert = await detect_mood_shift(
                contact_id=peer_id,
                owner_telegram_id=owner_telegram_id,
            )
            if alert:
                alerts.append(alert)
                logger.info(
                    "check_mood_alerts: alert для контакта %r (peer_id=%d)",
                    display_name,
                    peer_id,
                )
        except Exception:
            logger.exception(
                "check_mood_alerts: ошибка для контакта peer_id=%d", peer_id
            )

    return alerts
