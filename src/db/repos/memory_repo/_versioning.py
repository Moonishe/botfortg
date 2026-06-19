"""Memory repository — memory versioning / audit trail."""

from __future__ import annotations

import logging
from datetime import datetime, UTC

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Memory, MemoryVersion, User

logger = logging.getLogger(__name__)


async def _save_version_core(
    session: AsyncSession,
    user: User,
    memory_id: int,
    fact_text: str,
    edited_by: str = "user",
    reason: str | None = None,
) -> MemoryVersion:
    """Lock-free core of save_memory_version (caller must hold per-user lock).

    Used internally by ``save_memory_version`` (which acquires the lock)
    and ``rollback_memory`` (which already holds the lock for the entire
    rollback operation to prevent TOCTOU races with concurrent
    ``_add_memory_core``).

    Args:
        session: Активная сессия БД.
        user: Owner (User ORM object).
        memory_id: ID факта памяти.
        fact_text: Текст факта на момент сохранения версии.
        edited_by: Кто внёс изменение ("user", "system", "agent").
        reason: Причина изменения (опционально).

    Returns:
        Созданный объект MemoryVersion.

    Raises:
        ValueError: если fact_text пуст или memory не принадлежит user.
    """
    # ── Validation ───────────────────────────────────────────────────
    fact_text = fact_text.strip()
    if not fact_text:
        logger.warning(
            "save_memory_version: empty fact_text for memory_id=%d "
            "- skipping version save",
            memory_id,
        )
        raise ValueError("fact_text must not be empty")

    # Ownership check
    mem = await session.get(Memory, memory_id)
    if mem is None or mem.user_id != user.id:
        logger.warning(
            "save_memory_version: memory_id=%d does not belong to user %d",
            memory_id,
            user.id,
        )
        raise ValueError("memory not found or not owned")

    # Получаем текущую максимальную версию для этого факта
    stmt = select(func.max(MemoryVersion.version)).where(
        MemoryVersion.memory_id == memory_id
    )
    result = await session.execute(stmt)
    max_ver: int = result.scalar() or 0

    version = MemoryVersion(
        memory_id=memory_id,
        version=max_ver + 1,
        fact_text=fact_text,
        edited_by=edited_by,
        reason=reason,
    )
    session.add(version)
    await session.flush()
    logger.debug(
        "Saved memory version v%d for memory_id=%d (edited_by=%s)",
        version.version,
        memory_id,
        edited_by,
    )
    return version


async def save_memory_version(
    session: AsyncSession,
    user: User,
    memory_id: int,
    fact_text: str,
    edited_by: str = "user",
    reason: str | None = None,
) -> MemoryVersion:
    """Сохранить версионный снимок факта памяти.

    Используется при каждом редактировании / деактивации / откате факта,
    чтобы сохранить полную историю изменений (audit trail).

    Приобретает per-user lock чтобы предотвратить гонку на
    ``MAX(version) + 1`` при параллельных вызовах (например, из
    ``apply_nudge_decision`` и ``check_contradiction_response``,
    которые не держат lock вызывающего).

    Args:
        session: Активная сессия БД.
        user: Owner (User ORM object).
        memory_id: ID факта памяти.
        fact_text: Текст факта на момент сохранения версии.
        edited_by: Кто внёс изменение ("user", "system", "agent").
        reason: Причина изменения (опционально).

    Returns:
        Созданный объект MemoryVersion.
    """
    from src.db.repos.session_repo import _get_user_lock

    lock = _get_user_lock(user.id)
    async with lock:
        return await _save_version_core(
            session,
            user,
            memory_id,
            fact_text,
            edited_by=edited_by,
            reason=reason,
        )


async def get_memory_history(
    session: AsyncSession,
    user: User,
    memory_id: int,
) -> list[MemoryVersion]:
    """Получить историю правок факта памяти (от новых к старым).

    Args:
        session: Активная сессия БД.
        user: Owner (User ORM object).
        memory_id: ID факта памяти.

    Returns:
        Список MemoryVersion, отсортированный по version DESC.
        Пустой список, если факт не найден или не принадлежит пользователю.
    """
    mem = await session.get(Memory, memory_id)
    if mem is None or mem.user_id != user.id:
        logger.warning(
            "get_memory_history: memory_id=%d does not belong to user %d",
            memory_id,
            user.id,
        )
        return []

    stmt = (
        select(MemoryVersion)
        .where(MemoryVersion.memory_id == memory_id)
        .order_by(MemoryVersion.version.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def rollback_memory(
    session: AsyncSession,
    user: User,
    memory_id: int,
    target_version: int,
) -> Memory | None:
    """Откатить факт памяти к указанной версии.

    Загружает текст из MemoryVersion с version == target_version,
    обновляет Memory.fact и сохраняет откат как новую версию
    (edited_by="system", reason="rollback to v{target_version}").

    Args:
        session: Активная сессия БД.
        user: Owner (User ORM object).
        memory_id: ID факта памяти.
        target_version: Номер версии, к которой нужно откатиться.

    Returns:
        Обновлённый объект Memory, или None если версия не найдена
        или факт не принадлежит пользователю.
    """
    from src.db.repos.session_repo import _get_user_lock

    lock = _get_user_lock(user.id)

    async with lock:
        # Ownership + liveness check
        mem = await session.get(Memory, memory_id)
        if mem is None or mem.user_id != user.id:
            logger.warning(
                "rollback_memory: memory_id=%d does not belong to user %d",
                memory_id,
                user.id,
            )
            return None
        if not mem.is_active:
            logger.warning(
                "rollback_memory: memory_id=%d is inactive — cannot rollback",
                memory_id,
            )
            return None

        # Получаем целевую версию
        stmt = select(MemoryVersion).where(
            MemoryVersion.memory_id == memory_id,
            MemoryVersion.version == target_version,
        )
        ver = (await session.execute(stmt)).scalar_one_or_none()
        if not ver:
            logger.warning(
                "rollback_memory: version v%d not found for memory_id=%d",
                target_version,
                memory_id,
            )
            return None

        mem.fact = ver.fact_text
        mem.updated_at = datetime.now(UTC)

        # Сохраняем откат как новую версию (через lock-free _save_version_core —
        # save_memory_version тоже берёт per-user lock, а asyncio.Lock
        # не реентерабелен — вызов save_memory_version здесь = deadlock).
        await _save_version_core(
            session,
            user,
            memory_id,
            ver.fact_text,
            edited_by="system",
            reason=f"rollback to v{target_version}",
        )

        await session.flush()
        logger.info("Rolled back memory_id=%d to v%d", memory_id, target_version)
        return mem
