"""Асинхронная очередь для фоновой обработки памяти.

Позволяет вынести сохранение, извлечение и тегирование фактов
из основного потока обработки сообщений в фоновый worker.
"""

import asyncio
import contextlib
import logging

from sqlalchemy.exc import SQLAlchemyError

from src.core.memory._queue_core import (
    MemoryJob,
    _queue,
    _retry_dlq,
    enqueue,  # pyright: ignore[reportUnusedImport] — re-exported for external callers
)
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.llm.base import TaskType
from src.llm.router import build_provider

logger = logging.getLogger(__name__)


_worker_task: asyncio.Task | None = None
# DLQ retry loop must be tracked too — otherwise it (a) becomes a zombie that
# keeps writing to a closing DB after shutdown, and (b) loses its only strong
# reference and may be garbage-collected mid-execution.
_dlq_task: asyncio.Task | None = None
_worker_lock: asyncio.Lock = asyncio.Lock()


async def _worker() -> None:
    """Фоновый обработчик очереди.

    Бесконечный цикл: забирает задание из очереди и выполняет.
    При крахе одной задачи не падает — логирует и идёт дальше.
    """
    while True:
        try:
            job: MemoryJob = await _queue.get()
            await _process_job(job)
            _queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Memory queue worker error")
            _queue.task_done()


async def _process_job(job: MemoryJob) -> None:
    """Выполнить одно задание."""
    async with get_session() as session:
        owner = await get_or_create_user(session, job.telegram_id)

        if job.job_type == "save":
            await _handle_save(session, owner, job)
        elif job.job_type == "extract":
            await _handle_extract(session, owner, job)
        elif job.job_type == "tag":
            await _handle_tag(session, owner, job)
        else:
            logger.warning("Unknown memory job type: %s", job.job_type)


async def _handle_save(session, owner, job: MemoryJob) -> None:
    """Сохранить готовые факты (job_type='save')."""
    from src.db.repo import link_memories
    from src.core.memory.memory_service import save_memory_single
    from src.core.actions.vector_store import get_vector_store

    facts = list(job.facts or [])
    from src.db.models import Memory

    saved_by_index: dict[int, Memory] = {}
    for i, fact_data in enumerate(facts):
        try:
            # savepoint: изолирует каждый факт — ошибка в одном не откатывает другие
            async with session.begin_nested():
                _vector_store = (
                    await get_vector_store() if fact_data.get("embedding") else None
                )
                mem = await save_memory_single(
                    session,
                    owner,
                    fact=fact_data.get("fact", ""),
                    contact_id=job.contact_id,
                    sentiment=fact_data.get("sentiment"),
                    source=fact_data.get("source") or job.source or "chat",
                    importance=fact_data.get("importance", 0.5),
                    decay_rate=fact_data.get("decay_rate", 0.07),
                    memory_type=fact_data.get("memory_type"),
                    embedding=fact_data.get("embedding"),
                    vector_store_obj=_vector_store,
                    confidence=0.5,
                )
            if mem:
                saved_by_index[i] = mem
        except Exception:
            logger.exception(
                "Failed to save fact for user %d, skipping", job.telegram_id
            )

    # Сохраняем связи между фактами, указанные LLM (relation_type / relation_to_index).
    # Каждая связь — в своём savepoint, чтобы ошибка в одной не откатывала другие.
    # Гарантируем, что все ID доступны: flush после цикла сохранения.
    await session.flush()
    for i, fact_data in enumerate(facts):
        source_memory = saved_by_index.get(i)
        if source_memory is None:
            continue
        relation_type = fact_data.get("relation_type")
        relation_to_index = fact_data.get("relation_to_index")
        if relation_type and relation_to_index is not None:
            try:
                target_idx = int(relation_to_index)
            except (TypeError, ValueError):
                continue
            target_memory = saved_by_index.get(target_idx)
            if target_memory is not None:
                try:
                    # savepoint: изолирует link — ошибка в одной связи
                    # не откатывает другие
                    async with session.begin_nested():
                        await link_memories(
                            session,
                            owner,
                            source_id=source_memory.id,
                            target_id=target_memory.id,
                            relation_type=relation_type,
                            weight=0.9,
                        )
                except Exception:
                    logger.exception(
                        "Failed to link memories %d -> %d, skipping",
                        source_memory.id,
                        target_memory.id,
                    )

    # --- Persona auto-rebuild: check if enough new personal facts ---
    try:
        from src.core.memory.persona_pipeline import maybe_rebuild_persona

        # Only trigger if we saved personal/self-facts
        has_personal_facts = any(
            fact_data.get("memory_type") in {"personal", "preference"}
            for fact_data in facts
        )
        if has_personal_facts:
            await maybe_rebuild_persona(session, owner)
    except Exception:
        logger.debug("Persona auto-rebuild skipped (non-critical)", exc_info=True)

    logger.debug(
        "Background saved %d/%d facts for user %d",
        len(saved_by_index),
        len(facts),
        job.telegram_id,
    )

    # ── Invalidate contact memory digest ────────────────────────────
    if job.contact_id is not None:
        try:
            from src.core.contacts.contact_memory_digest import (
                invalidate_contact_digest,
            )

            await invalidate_contact_digest(job.contact_id)
        except Exception:
            logger.debug(
                "Failed to invalidate digest for peer %d",
                job.contact_id,
                exc_info=True,
            )


async def _handle_extract(session, owner, job: MemoryJob) -> None:
    """Извлечь и сохранить факты из текста переписки (job_type='extract')."""
    from src.core.memory.memory_extractor import extract_and_save_memories

    provider = await build_provider(session, owner, task_type=TaskType.MEMORY)
    if provider is None:
        logger.warning("No provider for extract job uid=%d", job.telegram_id)
        return

    # Получить объект Contact по peer_id
    contact = None
    if job.contact_id is not None:
        from sqlalchemy import select
        from src.db.models import Contact

        result = await session.execute(
            select(Contact).where(
                Contact.user_id == owner.id,
                Contact.peer_id == job.contact_id,
            )
        )
        contact = result.scalar_one_or_none()

    # Вызвать extract_and_save_memories — она сделает LLM-вызов и
    # поставит задачу на сохранение в ту же очередь (job_type='save')
    count = await extract_and_save_memories(
        provider,
        job.telegram_id,
        contact,
        messages=[],
        transcript=job.messages_text,
    )

    # --- Persona auto-rebuild ---
    try:
        from src.core.memory.persona_pipeline import maybe_rebuild_persona

        await maybe_rebuild_persona(session, owner)
    except Exception:
        logger.debug("Persona auto-rebuild skipped (non-critical)", exc_info=True)

    logger.debug(
        "Background extracted %d facts for user %d (contact %s)",
        count,
        job.telegram_id,
        job.contact_id,
    )


async def _handle_tag(session, owner, job: MemoryJob) -> None:
    """Протегировать нетэгированные факты (job_type='tag')."""
    from src.core.memory.memory_tagger import tag_new_fact
    from src.db.repo import list_memories

    provider = await build_provider(session, owner, task_type=TaskType.MEMORY)
    if provider is None:
        logger.warning("No provider for tag job uid=%d", job.telegram_id)
        return

    memories = await list_memories(
        session, owner, is_active=True, has_tags=False, limit=30
    )
    tagged = 0
    MAX_TAG_PER_CYCLE = 30
    for mem in memories:
        if tagged >= MAX_TAG_PER_CYCLE:
            logger.debug("_handle_tag: hit limit %d, stopping", MAX_TAG_PER_CYCLE)
            break
        try:
            await tag_new_fact(provider, session, mem.id)
            # L8: commit() после каждого успешного тегирования —
            # tag_new_fact может оставить session в dirty-состоянии
            # (flush-only изменения), и без commit следующая итерация
            # может увидеть stale данные. rollback() в except —
            # откатывает грязные изменения после ошибки тегирования,
            # возвращая session в чистое состояние для следующего факта.
            await session.commit()
            tagged += 1
        except (ValueError, AttributeError, ConnectionError, OSError, SQLAlchemyError):
            await session.rollback()
            logger.exception("Tagging failed for memory %d", mem.id)
    logger.debug(
        "Background tagging done for user %d (%d tagged)", job.telegram_id, tagged
    )


def _spawn_dlq_task() -> None:
    """Create the DLQ retry task and attach a crash-restart callback.

    Must NOT be called outside _worker_lock — otherwise it races with
    stop_worker() which cancels + sets _dlq_task = None.
    """
    if not _worker_lock.locked():
        raise RuntimeError("_spawn_dlq_task must be called under _worker_lock")
    global _dlq_task
    if _dlq_task is None or _dlq_task.done():
        _dlq_task = asyncio.create_task(_retry_dlq(), name="memory-dlq-retry")
        _dlq_task.add_done_callback(_on_dlq_done)


def _on_dlq_done(task: asyncio.Task) -> None:
    """Log unexpected DLQ task death and schedule a restart."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.exception("DLQ retry loop died unexpectedly", exc_info=exc)
        # Restart after a short delay to avoid tight crash loops.
        # Must acquire _worker_lock to avoid racing with stop_worker().
        loop = asyncio.get_event_loop()

        def _restart() -> None:
            if not _worker_lock.locked():
                _ = loop.create_task(_restart_under_lock())

        async def _restart_under_lock() -> None:
            async with _worker_lock:
                _spawn_dlq_task()

        loop.call_later(5, _restart)


async def start_worker() -> asyncio.Task:
    """Запустить фонового worker'а и DLQ retry loop (если ещё не запущены)."""
    global _worker_task
    async with _worker_lock:
        if _worker_task is None or _worker_task.done():
            _worker_task = asyncio.create_task(_worker(), name="memory-queue-worker")
            # DLQ retry loop — re-injects overflowed jobs every 30s.
            # Only (re)spawn if not already running — avoids duplicate DLQ
            # loops when start_worker is called after a previous stop_worker.
            _spawn_dlq_task()
        return _worker_task


async def stop_worker() -> None:
    """Остановить фонового worker'а и DLQ loop (graceful shutdown)."""
    global _worker_task, _dlq_task
    async with _worker_lock:
        if _worker_task and not _worker_task.done():
            _worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _worker_task
            _worker_task = None
            logger.info("Memory queue worker stopped")
        if _dlq_task and not _dlq_task.done():
            _dlq_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _dlq_task
            _dlq_task = None
            logger.info("Memory DLQ retry loop stopped")
