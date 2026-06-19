"""Асинхронная очередь уведомлений с группировкой по topic + category."""

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, UTC
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update

from src.core.infra.notifier import notifier
from src.core.infra.task_manager import track_ff
from src.core.infra.text_sanitizer import sanitize_html
from src.core.infra.timeutil import ensure_utc
from src.db.models import Notification
from src.db.session import SessionLocal

if TYPE_CHECKING:
    from aiogram.types import InlineKeyboardMarkup


logger = logging.getLogger(__name__)

_notification_queue_guard = asyncio.Lock()


class NotificationQueue:
    """
    Асинхронная очередь уведомлений с группировкой по topic + category.

    Правила:
    - Уведомления одной topic в окне 5 минут — группируются в один batch
    - Приоритет: CRITICAL → немедленно (без очереди)
    - HIGH/MEDIUM/LOW → группируются, отправляются раз в 60 секунд
    - Максимум 10 уведомлений в одном batch-сообщении
    - TTL 24 часа для недоставленных
    - Уведомления с reply_markup (inline-клавиатуры) отправляются немедленно
    """

    def __init__(self) -> None:
        self._window_seconds = 300  # 5 минут
        self._flush_interval = 60  # проверка раз в минуту
        self._max_batch_size = 10
        self._ttl_hours = 24
        self._loop_task: asyncio.Task | None = None

    async def enqueue(
        self,
        topic: str,
        text: str,
        priority: int = Notification.PRIORITY_MEDIUM,
        category: str = "",
        metadata: dict | None = None,
        reply_markup: "InlineKeyboardMarkup | None" = None,
    ) -> int:
        """
        Добавить уведомление в очередь.

        CRITICAL (priority=0) или уведомления с reply_markup —
        отправляются немедленно, минуя очередь.

        Возвращает notification_id (0 для немедленно отправленных).
        """
        # Normalize and validate priority
        priority = int(priority)
        priority = max(
            Notification.PRIORITY_CRITICAL,
            min(Notification.PRIORITY_LOW, priority),
        )
        topic = (topic or "general").strip()
        text = (text or "").strip()
        if not text:
            logger.warning("Ignoring empty notification text for topic %s", topic)
            return 0

        # Уведомления с inline-клавиатурами — немедленная отправка
        if reply_markup is not None:
            await notifier.notify(text, reply_markup=reply_markup)
            return 0

        # Критические — немедленная отправка
        if priority == Notification.PRIORITY_CRITICAL:
            await notifier.notify(text)
            return 0

        async with SessionLocal() as session:
            notif = Notification(
                topic=topic,
                priority=priority,
                category=category or topic,
                text=text,
                metadata_json=metadata or {},
            )
            session.add(notif)
            await session.commit()
            await session.refresh(notif)
            return notif.id

    async def flush(self) -> int:
        """
        Группирует и отправляет непрочитанные уведомления.
        Группировка: все уведомления одной topic за последние window_seconds.
        Возвращает количество обработанных.
        """
        async with _notification_queue_guard:
            async with SessionLocal() as session:
                # SQLite не поддерживает SELECT ... FOR UPDATE.
                # Вместо этого: атомарно резервируем pending-уведомления
                # через UPDATE с уникальным batch_id, затем SELECT зарезервированных.
                # asyncio.Lock (_notification_queue_guard) обеспечивает сериализацию
                # на уровне приложения (single-instance deployment).
                batch_id = uuid.uuid4().hex[:12]

                # Шаг 1: атомарно пометить все pending-уведомления текущим batch_id
                await session.execute(
                    update(Notification)
                    .where(Notification.flushed_at.is_(None))
                    .values(batch_id=batch_id)
                )
                await session.commit()

                # Шаг 2: выбрать только зарезервированные в этом тике
                result = await session.execute(
                    select(Notification)
                    .where(
                        Notification.flushed_at.is_(None),
                        Notification.batch_id == batch_id,
                    )
                    .order_by(
                        Notification.topic,
                        Notification.priority,
                        Notification.created_at,
                    )
                )
                pending = list(result.scalars().all())

                if not pending:
                    return 0

                # Разделяем: свежие (в окне) — группируем, старые — отправляем по одному
                window_start = datetime.now(UTC) - timedelta(
                    seconds=self._window_seconds
                )
                fresh: list[Notification] = []
                stale: list[Notification] = []
                for n in pending:
                    created = ensure_utc(n.created_at)
                    if created is not None and created >= window_start:
                        fresh.append(n)
                    else:
                        stale.append(n)

                # Группировка свежих по (topic, priority_bucket)
                groups: dict[str, list[Notification]] = defaultdict(list)
                for n in fresh:
                    if n.priority == Notification.PRIORITY_HIGH:
                        bucket = "high"
                    elif n.priority == Notification.PRIORITY_MEDIUM:
                        bucket = "medium"
                    else:
                        bucket = "low"
                    key = f"{n.topic}:{bucket}"
                    groups[key].append(n)

                # Старые — каждое в своей группе для немедленной отправки
                for n in stale:
                    key = f"{n.topic}:stale_{n.id}"
                    groups[key] = [n]

                total_flushed = 0

                for key, group in groups.items():
                    topic = key.split(":", 1)[0]
                    batch = group[: self._max_batch_size]
                    text = self._format_batch(batch)

                    try:
                        await notifier.notify(text)
                        total_flushed += len(batch)
                    except Exception:
                        logger.exception("Failed to send batch for topic %s", topic)
                        continue

                    # Помечаем отправленные как flushed.
                    ids = [n.id for n in batch]
                    await session.execute(
                        update(Notification)
                        .where(Notification.id.in_(ids))
                        .values(
                            flushed_at=datetime.now(UTC),
                        )
                    )
                    await session.commit()

                return total_flushed

    def _format_batch(self, notifications: list[Notification]) -> str:
        """Форматирует сгруппированные уведомления в одно сообщение."""
        count = len(notifications)

        # Одно уведомление — упрощённый формат: без заголовка, без сводки
        if count == 1:
            return notifications[0].text

        # Группируем по приоритету внутри batch
        by_priority: dict[int, list[Notification]] = defaultdict(list)
        for n in notifications:
            by_priority[n.priority].append(n)

        priority_emoji = {
            Notification.PRIORITY_CRITICAL: "🔴",
            Notification.PRIORITY_HIGH: "🟠",
            Notification.PRIORITY_MEDIUM: "🟡",
            Notification.PRIORITY_LOW: "🟢",
        }

        _topic_ru: dict[str, str] = {
            "system": "Техническое",
            "digest": "дайджест",
            "news": "новости",
            "reminder": "напоминания",
            "task_manager": "задачи",
            "skills": "навыки",
            "memory": "память",
            "contacts": "контакты",
            "general": "Общее",
        }

        # Эмодзи-иконки для русских названий тем
        _topic_display: dict[str, str] = {
            "Техническое": "⚙️ Техническое",
            "память": "🧠 Память",
            "диск": "💾 Диск",
            "ошибка": "❌ Ошибка",
            "обновление": "🔄 Обновление",
            "дайджест": "📰 Дайджест",
            "новости": "📰 Новости",
            "напоминания": "⏰ Напоминания",
            "задачи": "📋 Задачи",
            "навыки": "🛠 Навыки",
            "контакты": "👥 Контакты",
            "Общее": "📋 Общее",
        }

        # Собираем разные темы внутри группы
        topic_set: set[str] = {n.category or n.topic for n in notifications}

        # Заголовок: показывать "(N тем, M уведомлений)" только если тем > 1
        if len(topic_set) > 1:
            header = f"📬 <b>Сводка</b> ({len(topic_set)} тем, {count} уведомлений)"
        else:
            header = "📬 <b>Сводка</b>"

        lines = [
            header,
            "─" * 28,
        ]

        for prio in sorted(by_priority):
            items = by_priority[prio]
            emoji = priority_emoji.get(prio, "⚪")
            sub_topic = items[0].category or items[0].topic or "general"
            sub_topic = _topic_ru.get(sub_topic, sub_topic)
            # Применяем эмодзи-оформление к русскому названию темы
            sub_topic = _topic_display.get(sub_topic, sub_topic)
            lines.append(f"{emoji} <b>{sub_topic}</b> ({len(items)})")
            for item in items:
                # Обрезаем длинный текст и экранируем HTML
                raw = item.text[:200]
                if len(item.text) > 200:
                    raw += "…"
                short = sanitize_html(raw)
                lines.append(f"• {short}")
            lines.append("")

        return "\n".join(lines)

    async def flush_loop(self) -> None:
        """Бесконечный цикл: flush() + периодическая очистка."""
        _cleanup_counter = 0
        while True:
            try:
                flushed = await self.flush()
                if flushed > 0:
                    logger.info("Flushed %d notifications", flushed)
            except asyncio.CancelledError:
                raise  # must propagate for clean shutdown
            except Exception:
                logger.exception("NotificationQueue flush error")

            # Очистка просроченных — раз в час (60 итераций при интервале 60с)
            _cleanup_counter += 1
            if _cleanup_counter >= 60:
                _cleanup_counter = 0
                try:
                    cleaned = await self.cleanup_expired()
                    if cleaned > 0:
                        logger.info("Cleaned %d expired notifications", cleaned)
                except asyncio.CancelledError:
                    raise  # must propagate for clean shutdown
                except Exception:
                    logger.exception("NotificationQueue cleanup error")

            await asyncio.sleep(self._flush_interval)

    def start(self) -> None:
        """Запустить фоновый цикл (идемпотентен)."""
        if self._loop_task is not None and not self._loop_task.done():
            logger.warning("NotificationQueue already running")
            return
        self._loop_task = asyncio.create_task(self.flush_loop())
        track_ff(self._loop_task)
        logger.info("NotificationQueue started (flush every %ds)", self._flush_interval)

    async def stop(self) -> None:
        """Остановить фоновый цикл."""
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    async def cleanup_expired(self) -> int:
        """Удаляет уведомления старше TTL. Возвращает количество удалённых."""
        cutoff = datetime.now(UTC) - timedelta(hours=self._ttl_hours)
        async with SessionLocal() as session:
            result = await session.execute(
                delete(Notification).where(
                    Notification.created_at < cutoff,
                )
            )
            await session.commit()
            return result.rowcount


# Глобальный синглтон (заменяет прямой вызов notifier.notify)
notification_queue = NotificationQueue()
