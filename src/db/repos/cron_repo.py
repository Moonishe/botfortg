"""CRUD для CronJob — гибких повторяющихся задач."""

from __future__ import annotations

import json
from datetime import datetime, UTC
from typing import Any

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models._cron import CronJob


async def create_cron_job(
    session: AsyncSession,
    user_id: int,
    name: str,
    cron_expression: str,
    payload_type: str = "message",
    payload: dict[str, Any] | None = None,
    *,
    description: str | None = None,
    timezone: str = "UTC",
    enabled: bool = True,
    channel: str = "notification_queue",
    notify_on_error: bool = True,
    max_runs: int = 0,
    max_run_date: datetime | None = None,
    tags: list[str] | None = None,
    next_run_at: datetime | None = None,
) -> CronJob:
    """Создать новую cron-задачу.

    Args:
        session: Асинхронная сессия SQLAlchemy.
        user_id: ID пользователя-владельца.
        name: Название задачи.
        cron_expression: 5-польное cron-выражение (например '0 9 * * 1-5').
        payload_type: Тип действия: 'message' | 'llm_prompt' | 'webhook' | 'callback'.
        payload: Параметры действия (будет сериализовано в JSON).
        description: Описание задачи.
        timezone: IANA-таймзона (по умолчанию 'UTC').
        enabled: Активна ли задача.
        channel: Канал доставки.
        notify_on_error: Уведомлять об ошибках.
        max_runs: Максимум выполнений (0 = без лимита).
        max_run_date: Максимальная дата выполнения (None = без лимита).
        tags: Список тегов для категоризации.
        next_run_at: Первое время выполнения (если None — будет
            рассчитано при старте шедулера).

    Returns:
        Созданный объект CronJob.
    """
    job = CronJob(
        user_id=user_id,
        name=name,
        description=description,
        cron_expression=cron_expression,
        timezone=timezone,
        enabled=enabled,
        payload_type=payload_type,
        payload=json.dumps(payload, ensure_ascii=False) if payload else None,
        channel=channel,
        notify_on_error=notify_on_error,
        max_runs=max_runs,
        max_run_date=max_run_date,
        next_run_at=next_run_at,
        tags=json.dumps(tags, ensure_ascii=False) if tags else None,
    )
    session.add(job)
    await session.flush()
    return job


async def get_cron_job(session: AsyncSession, job_id: int) -> CronJob | None:
    """Получить задачу по ID."""
    result = await session.execute(select(CronJob).where(CronJob.id == job_id))
    return result.scalar_one_or_none()


async def get_cron_job_for_update(session: AsyncSession, job_id: int) -> CronJob | None:
    """Получить задачу по ID с блокировкой для обновления (FOR UPDATE).

    Используется в шедулере для атомарного захвата due-задач.
    """
    result = await session.execute(
        select(CronJob).where(CronJob.id == job_id).with_for_update()
    )
    return result.scalar_one_or_none()


async def update_cron_job(
    session: AsyncSession,
    job_id: int,
    **kwargs: Any,
) -> CronJob | None:
    """Обновить поля задачи.

    Args:
        session: Асинхронная сессия.
        job_id: ID задачи.
        **kwargs: Поля для обновления (name, cron_expression, enabled и т.д.).

    Returns:
        Обновлённый объект или None если задача не найдена.
    """
    # Сериализуем JSON-поля если переданы как dict/list
    if "payload" in kwargs and isinstance(kwargs["payload"], dict):
        kwargs["payload"] = json.dumps(kwargs["payload"], ensure_ascii=False)
    if "tags" in kwargs and isinstance(kwargs["tags"], list):
        kwargs["tags"] = json.dumps(kwargs["tags"], ensure_ascii=False)

    kwargs["updated_at"] = datetime.now(UTC)

    result = await session.execute(
        update(CronJob).where(CronJob.id == job_id).values(**kwargs).returning(CronJob)
    )
    await session.flush()
    return result.scalar_one_or_none()


async def delete_cron_job(session: AsyncSession, job_id: int) -> bool:
    """Удалить задачу. Возвращает True если задача существовала."""
    result = await session.execute(delete(CronJob).where(CronJob.id == job_id))
    await session.flush()
    return result.rowcount > 0


async def get_due_jobs(
    session: AsyncSession,
    *,
    limit: int | None = None,
) -> list[CronJob]:
    """Получить задачи, время выполнения которых наступило.

    Критерии:
        - enabled = True
        - next_run_at IS NOT NULL
        - next_run_at <= now (UTC)
        - (max_runs == 0 OR run_count < max_runs)
        - (max_run_date IS NULL OR max_run_date > now)

    Args:
        session: Асинхронная сессия.
        limit: Максимальное количество результатов (None = без лимита).

    Returns:
        Список задач, отсортированный по next_run_at (самые
        «просроченные» первыми).
    """
    now = datetime.now(UTC)
    query = (
        select(CronJob)
        .where(CronJob.enabled == True)
        .where(CronJob.next_run_at.isnot(None))
        .where(CronJob.next_run_at <= now)
        .where((CronJob.max_runs == 0) | (CronJob.run_count < CronJob.max_runs))
        .where((CronJob.max_run_date.is_(None)) | (CronJob.max_run_date > now))
        .order_by(CronJob.next_run_at.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def advance_job(
    session: AsyncSession,
    job_id: int,
    next_run_at: datetime | None,
) -> CronJob | None:
    """Атомарно обновить задачу после выполнения.

    Инкрементирует run_count (SQL-уровень), обновляет last_run_at,
    next_run_at. Проверяет pre-conditions в WHERE:
    - enabled = True
    - max_runs не исчерпан
    - max_run_date не истёк

    Если next_run_at is None — задача завершена (enabled=False, next_run_at=NULL).
    Если pre-conditions нарушены (задача была отключена между get_due_jobs
    и advance_job) — возвращает None, update не выполняется.

    Args:
        session: Асинхронная сессия.
        job_id: ID задачи.
        next_run_at: Следующее время выполнения (None = задача завершена).

    Returns:
        Обновлённый объект CronJob или None.
    """
    now = datetime.now(UTC)

    # Pre-conditions: задача всё ещё активна
    stmt = (
        update(CronJob)
        .where(CronJob.id == job_id)
        .where(CronJob.enabled == True)
        .where((CronJob.max_runs == 0) | (CronJob.run_count < CronJob.max_runs))
        .where((CronJob.max_run_date.is_(None)) | (CronJob.max_run_date > now))
    )

    if next_run_at is None:
        stmt = stmt.values(
            run_count=CronJob.run_count + 1,
            last_run_at=now,
            next_run_at=None,
            enabled=False,
            updated_at=now,
        )
    else:
        stmt = stmt.values(
            run_count=CronJob.run_count + 1,
            last_run_at=now,
            next_run_at=next_run_at,
            updated_at=now,
        )

    result = await session.execute(stmt.returning(CronJob))
    await session.flush()
    return result.scalar_one_or_none()


async def list_user_jobs(
    session: AsyncSession,
    user_id: int,
    *,
    enabled_only: bool = False,
    tag: str | None = None,
) -> list[CronJob]:
    """Получить список задач пользователя.

    Args:
        session: Асинхронная сессия.
        user_id: ID пользователя.
        enabled_only: Только активные задачи.
        tag: Фильтр по тегу.

    Returns:
        Список CronJob пользователя.
    """
    query = select(CronJob).where(CronJob.user_id == user_id)

    if enabled_only:
        query = query.where(CronJob.enabled == True)

    if tag:
        # autoescape=True защищает от LIKE-инъекций (% и _ в tag пользователя)
        query = query.where(CronJob.tags.contains(tag, autoescape=True))

    query = query.order_by(CronJob.next_run_at.asc().nullslast())
    result = await session.execute(query)
    return list(result.scalars().all())


async def bulk_disable_expired(session: AsyncSession) -> int:
    """Отключить задачи у которых max_run_date истёк.

    Returns:
        Количество отключённых задач.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        update(CronJob)
        .where(CronJob.enabled == True)
        .where(CronJob.max_run_date.isnot(None))
        .where(CronJob.max_run_date <= now)
        .values(enabled=False, updated_at=now)
    )
    await session.flush()
    return result.rowcount
