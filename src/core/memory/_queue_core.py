"""Core queue primitives for memory background processing.

Houses MemoryJob, the async queue, and enqueue() — extracted from
memory_queue.py to break the memory_queue ↔ memory_extractor cycle.
All other modules import from here instead of from each other.
"""

import asyncio
import logging
import json
from dataclasses import dataclass
from pathlib import Path

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
    retry_count: int = 0


# Очередь заданий (configurable maxsize — защита от переполнения памяти).
# Clamp to at least 1 to keep the queue bounded; 0 would create an unbounded queue.
_QUEUE_MAXSIZE = max(1, settings.memory_queue_maxsize)
_queue: asyncio.Queue[MemoryJob] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)

# Dead Letter Queue — задания, не поместившиеся в основную очередь.
# Периодически ре-инжектируются при освобождении места.
_dlq: list[MemoryJob] = []
_DLQ_MAX = 50
# ponytail: file-based journal for overflow jobs. Simple append, no DB.
_DLQ_JOURNAL_PATH = Path("data/dlq_overflow.jsonl")
_dlq_lock = asyncio.Lock()
# ponytail: overlap guard for _retry_dlq (30s interval); asyncio.Lock is sufficient
_dlq_overlap_guard = asyncio.Lock()

# In-memory overflow buffer for jobs that couldn't be journaled to disk.
# Prevents permanent data loss when filesystem is full/unavailable.
_dlq_overflow_buffer: list[MemoryJob] = []
_DLQ_OVERFLOW_MAX = 200
# ponytail: hard cap at 200 jobs (~40KB); drop oldest if exceeded.
# Upgrade path: spill to alternative disk path or external queue if sustained
# disk failure exceeds this buffer.


async def get_queue_stats() -> dict[str, int]:
    """Return a read-only snapshot of memory queue pressure."""
    async with _dlq_lock:
        dlq_size = len(_dlq)
        dlq_overflow = len(_dlq_overflow_buffer)
    return {
        "size": _queue.qsize(),
        "max_size": _queue.maxsize,
        "dlq_size": dlq_size,
        "dlq_max_size": _DLQ_MAX,
        "dlq_overflow": dlq_overflow,
    }


async def _retry_dlq() -> None:
    """Фоновый retry: перекладывает задания из DLQ в основную очередь."""
    while True:
        await asyncio.sleep(settings.memory_queue_poll_interval)
        async with _dlq_overlap_guard:
            try:
                # P6 fix: collect jobs to journal INSIDE lock, do I/O OUTSIDE lock.
                # Was: asyncio.to_thread(_write_to_dlq_journal) under _dlq_lock
                # → enqueue() callers blocked for entire journal write duration.
                jobs_to_journal: list[tuple[MemoryJob, str]] = []  # (job, source)
                async with _dlq_lock:
                    retried = 0
                    while _dlq:
                        job = _dlq.pop(0)
                        job.retry_count += 1
                        try:
                            _queue.put_nowait(job)
                            retried += 1
                        except asyncio.QueueFull:
                            # Queue filled between check and put; push job back to DLQ
                            _dlq.insert(0, job)
                            break
                    dropped = 0
                    if _dlq:
                        # Evict jobs with lowest retry_count (least-tried = least valuable)
                        while len(_dlq) > _DLQ_MAX:
                            # ponytail: evict least-tried job instead of oldest
                            min_idx = min(
                                range(len(_dlq)),
                                key=lambda i: _dlq[i].retry_count,
                            )
                            evicted = _dlq.pop(min_idx)
                            jobs_to_journal.append((evicted, "retry"))
                            dropped += 1
                    # Drain overflow buffer: try journal or re-inject to DLQ.
                    # Must be inside _dlq_lock — enqueue() also writes to
                    # _dlq_overflow_buffer under the same lock.
                    if _dlq_overflow_buffer:
                        drained = 0
                        while _dlq_overflow_buffer and len(_dlq) < _DLQ_MAX:
                            job = _dlq_overflow_buffer.pop(0)
                            _dlq.append(job)
                            drained += 1
                        # Collect remaining overflow items for journaling outside lock
                        while _dlq_overflow_buffer:
                            job = _dlq_overflow_buffer.pop(0)
                            jobs_to_journal.append((job, "overflow_drain"))
                        if drained:
                            logger.info(
                                "DLQ: drained %d jobs from overflow buffer",
                                drained,
                            )
                    dlq_remaining = len(_dlq)

                # Journal OUTSIDE lock — enqueue() can proceed concurrently
                re_overflow: list[tuple[MemoryJob, str]] = []
                for job, source in jobs_to_journal:
                    ok = await asyncio.to_thread(
                        _write_to_dlq_journal,
                        {
                            "job_type": job.job_type,
                            "telegram_id": job.telegram_id,
                            "contact_id": job.contact_id,
                            "retry_count": job.retry_count,
                        },
                    )
                    if not ok:
                        re_overflow.append((job, source))

                # Re-add failed journal jobs to overflow buffer (under lock)
                if re_overflow:
                    async with _dlq_lock:
                        for job, source in re_overflow:
                            _overflow_buffer_append(job, source=source)

                if retried:
                    logger.info(
                        "DLQ: re-injected %d jobs, %d remaining",
                        retried,
                        dlq_remaining,
                    )
                if dropped:
                    logger.warning(
                        "DLQ overflow: dropped %d least-retried jobs (journaled to %s)",
                        dropped,
                        _DLQ_JOURNAL_PATH,
                    )
            except Exception:
                logger.exception("_retry_dlq failed, will retry")
                await asyncio.sleep(1)


def _write_to_dlq_journal(job: dict) -> bool:
    """Ponytail: file-based journal for overflow jobs. Simple append, no DB.

    Returns True if write succeeded, False if it failed.
    On failure, caller keeps the job in a secondary in-memory overflow list
    to prevent permanent data loss.

    Ceiling: asyncio.to_thread wraps the sync I/O, and callers do journal
    writes OUTSIDE _dlq_lock (P6 fix). Lock contention with enqueue() is
    minimized — only the _overflow_buffer_append re-acquisition holds lock briefly.
    """
    try:
        _DLQ_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_DLQ_JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(job, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:
        logger.error(
            "DLQ journal write failed — job retained in overflow buffer: %s", job
        )
        return False


def _overflow_buffer_append(job: MemoryJob, *, source: str) -> None:
    """Append job to overflow buffer with cap enforcement. Caller holds _dlq_lock."""
    if job is None:
        logger.warning("DLQ overflow buffer: ignoring None job in %s", source)
        return
    _dlq_overflow_buffer.append(job)
    while len(_dlq_overflow_buffer) > _DLQ_OVERFLOW_MAX:
        dropped = _dlq_overflow_buffer.pop(0)
        logger.error(
            "DLQ overflow buffer cap (%d) exceeded in %s — dropping oldest job %s",
            _DLQ_OVERFLOW_MAX,
            source,
            dropped.job_type,
        )


async def enqueue(job: MemoryJob) -> None:
    """Добавить задание в очередь (с таймаутом из settings.memory_queue_put_timeout).

    Если очередь переполнена — отправитель ждёт до таймаута,
    после чего задание отбрасывается с error-логом.
    B5: timeout увеличен с 10с до 30с (настраивается), добавлен лог с размером очереди.
    """
    timeout = max(1, settings.memory_queue_put_timeout)
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
        # P6 fix: do journal I/O OUTSIDE lock to reduce contention
        needs_journal = False
        async with _dlq_lock:
            if len(_dlq) < _DLQ_MAX:
                _dlq.append(job)
            else:
                needs_journal = True

        if needs_journal:
            journaled = await asyncio.to_thread(
                _write_to_dlq_journal,
                {
                    "job_type": job.job_type,
                    "telegram_id": job.telegram_id,
                    "contact_id": job.contact_id,
                },
            )
            if not journaled:
                # Last resort: keep in memory to prevent permanent loss
                async with _dlq_lock:
                    _overflow_buffer_append(job, source="enqueue")
                logger.error(
                    "DLQ overflow + journal fail — job retained in overflow buffer: %s",
                    job.job_type,
                )
            else:
                logger.error(
                    "DLQ overflow — job %s journaled to %s",
                    job.job_type,
                    _DLQ_JOURNAL_PATH,
                )
