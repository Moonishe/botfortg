"""Core queue primitives for memory background processing.

Houses MemoryJob, the async queue, and enqueue() — extracted from
memory_queue.py to break the memory_queue ↔ memory_extractor cycle.
All other modules import from here instead of from each other.
"""

import asyncio
import logging
from dataclasses import dataclass

from src.config import settings

logger = logging.getLogger(__name__)


class MemoryQueueFullError(Exception):
    """Очередь памяти переполнена — задание не может быть добавлено."""

    pass


@dataclass
class MemoryJob:
    """Задача на фоновую обработку памяти.

    telegram_id — Telegram ID владельца (message.from_user.id).
    contact_id — Contact.peer_id (Telegram peer_id собеседника).
    facts — список словарей с фактами для сохранения.
    messages_text — текст переписки для извлечения фактов.
    job_type — тип задачи: save | extract | tag.
    source — источник данных ("chat", "dream", "import", "manual").
    """

    telegram_id: int
    contact_id: int | None = None
    facts: list[dict] | None = None
    messages_text: str = ""
    job_type: str = "save"
    source: str = "chat"


# Очередь заданий (configurable maxsize — защита от переполнения памяти)
_queue: asyncio.Queue[MemoryJob] = asyncio.Queue(maxsize=settings.memory_queue_maxsize)


async def enqueue(job: MemoryJob) -> None:
    """Добавить задание в очередь (с таймаутом из settings.memory_queue_put_timeout).

    Если очередь переполнена — отправитель ждёт до таймаута,
    после чего задание отбрасывается с error-логом.
    B5: timeout увеличен с 10с до 30с (настраивается), добавлен лог с размером очереди.
    """
    timeout = settings.memory_queue_put_timeout
    try:
        await asyncio.wait_for(_queue.put(job), timeout=timeout)
    except TimeoutError:
        logger.error(
            "Queue full (size=%d, max=%d), dropping job %s after %.0fs timeout",
            _queue.qsize(),
            _queue.maxsize,
            job.job_type,
            timeout,
        )
        raise MemoryQueueFullError(
            f"Очередь памяти переполнена (size={_queue.qsize()}, "
            f"max={_queue.maxsize}), задание {job.job_type} отброшено "
            f"после {timeout:.0f}s таймаута"
        ) from None
