"""Репозиторий ResearchJob — CRUD-операции с персистентным хранилищем."""

from __future__ import annotations

import logging
from datetime import datetime, UTC

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models._research import ResearchJob

logger = logging.getLogger(__name__)

async def save_research_job(session: AsyncSession, job: ResearchJob) -> None:
    """Сохранить новую задачу исследования в БД.

    Выполняет ``session.add()`` + ``session.flush()`` для немедленной

    фиксации PK перед коммитом текущей транзакции.

    Args:

        session: Активная SQLAlchemy async-сессия.

        job: ORM-объект ResearchJob с заполненными полями.

    """

    session.add(job)

    await session.flush()

    logger.debug("ResearchJob %s saved to DB", job.job_id)

async def get_research_job(session: AsyncSession, job_id: str) -> ResearchJob | None:
    """Получить задачу исследования по job_id.

    Args:

        session: Активная SQLAlchemy async-сессия.

        job_id: 12-символьный hex-идентификатор задачи.

    Returns:

        ResearchJob или None, если задача не найдена в БД.

    """

    result = await session.execute(
        select(ResearchJob).where(ResearchJob.job_id == job_id)
    )

    job = result.scalar_one_or_none()

    if job is not None:
        logger.debug("ResearchJob %s loaded from DB (status=%s)", job_id, job.status)

    return job

async def update_research_job(
    session: AsyncSession, job_id: str, **fields: object
) -> None:
    """Обновить поля задачи исследования в БД.

    Использует SQLAlchemy ``update()`` для атомарного изменения

    (без предварительной загрузки ORM-объекта).

    Args:

        session: Активная SQLAlchemy async-сессия.

        job_id: 12-символьный hex-идентификатор задачи.

        **fields: Поля для обновления (только существующие колонки).

    """

    # Фильтруем только поля, существующие в ORM-модели

    valid_fields = {}

    for k, v in fields.items():
        if hasattr(ResearchJob, k):
            valid_fields[k] = v

        else:
            logger.warning("Skipping unknown field %r for ResearchJob %s", k, job_id)

    if not valid_fields:
        return

    # Всегда обновляем updated_at

    valid_fields["updated_at"] = datetime.now(UTC)

    stmt = (
        update(ResearchJob).where(ResearchJob.job_id == job_id).values(**valid_fields)
    )

    await session.execute(stmt)

    await session.flush()

    logger.debug("ResearchJob %s updated in DB: %s", job_id, list(valid_fields.keys()))

async def delete_research_job(session: AsyncSession, job_id: str) -> bool:
    """Удалить задачу исследования из БД.

    Args:

        session: Активная SQLAlchemy async-сессия.

        job_id: 12-символьный hex-идентификатор задачи.

    Returns:

        True — задача найдена и удалена, False — задача не найдена.

    """

    job = await get_research_job(session, job_id)

    if job is not None:
        await session.delete(job)

        await session.flush()

        logger.debug("ResearchJob %s deleted from DB", job_id)

        return True

    return False

async def list_stale_jobs(
    session: AsyncSession, stale_seconds: float = 3600.0
) -> list[ResearchJob]:
    """Найти задачи, висящие в незавершённом статусе дольше TTL.

    Используется для восстановления после краша: задачи со статусами

    PENDING/PHASE1_RUNNING/PHASE2_RUNNING, которые не обновлялись

    дольше ``stale_seconds``, помечаются как FAILED.

    Args:

        session: Активная SQLAlchemy async-сессия.

        stale_seconds: TTL в секундах (по умолчанию 1 час).

    Returns:

        Список ORM-объектов ResearchJob с зависшими статусами.

    """

    cutoff = datetime.now(UTC).timestamp() - stale_seconds

    cutoff_dt = datetime.fromtimestamp(cutoff, tz=UTC)

    result = await session.execute(
        select(ResearchJob).where(
            ResearchJob.status.in_(["pending", "phase1_running", "phase2_running"]),
            ResearchJob.updated_at < cutoff_dt,
        )
    )

    jobs = list(result.scalars().all())

    if jobs:
        logger.info("Found %d stale research jobs in DB", len(jobs))

    return jobs
