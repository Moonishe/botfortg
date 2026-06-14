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

# Dead Letter Queue — задания, не поместившиеся в основную очередь.
# Периодически ре-инжектируются при освобождении места.
_dlq: list[MemoryJob] = []
_DLQ_MAX = 50
_dlq_lock = asyncio.Lock()


async def _retry_dlq() -> None:
    """Фоновый retry: перекладывает задания из DLQ в основную очередь."""
    while True:
        await asyncio.sleep(30)
        async with _dlq_lock:
            retried = 0
            while _dlq and not _queue.full():
                job = _dlq.pop(0)
                _queue.put_nowait(job)
                retried += 1
            if _dlq:
                # Trim oldest if DLQ overflows
                while len(_dlq) > _DLQ_MAX:
                    _dlq.pop(0)
                    retried -= 1
        if retried:
            logger.info("DLQ: re-injected %d jobs, %d remaining", retried, len(_dlq))


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
        logger.warning(
            "Queue full (size=%d, max=%d), moving job %s to DLQ",
            _queue.qsize(),
            _queue.maxsize,
            job.job_type,
        )
        # Move to Dead Letter Queue — will be retried when space frees up
        async with _dlq_lock:
            if len(_dlq) < _DLQ_MAX:
                _dlq.append(job)
            else:
                logger.error("DLQ overflow — job %s permanently dropped", job.job_type)
