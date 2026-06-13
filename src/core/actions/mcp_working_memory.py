"""MCP Tool: рабочая память (scratchpad) — key-value store для промежуточных результатов.
Используется LLM для запоминания данных между вызовами инструментов в multi-step задачах.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from sqlalchemy import delete, select

from src.core.actions.tool_registry import tool
from src.db.models._memory import WorkingMemory
from src.db.session import get_session

logger = logging.getLogger(__name__)

_DEFAULT_TTL_MINUTES: int = 60


# ── Хелпер: резолвим user_id (внутренний PK) из kwargs ──


def _resolve_user_id(kwargs: dict[str, Any]) -> int | None:
    """Извлекает user.id (внутренний первичный ключ) из runtime kwargs.

    В kwargs может быть передан как User ORM-объект, так и telegram_id (int).
    Возвращает user.id или None при ошибке.
    """
    _user_val = kwargs.get("user")
    if _user_val is None:
        return None
    if hasattr(_user_val, "id"):
        return int(_user_val.id)  # type: ignore[union-attr]
    try:
        return int(_user_val)
    except (TypeError, ValueError):
        return None


# ── Инструменты ──


@tool(
    name="write_memory",
    description=(
        "Сохраняет промежуточный результат в рабочую память (scratchpad). "
        "Используй когда нужно запомнить данные между шагами внутри одной задачи. "
        "Ключ и значение — строки. По умолчанию TTL = 60 минут."
    ),
    category="memory",
    risk="medium",
    params={
        "key": "str — уникальный ключ для сохранения",
        "value": "str — сохраняемое значение",
        "ttl_minutes": "int=60 — время жизни в минутах",
    },
)
async def write_working_memory(
    key: str,
    value: str,
    ttl_minutes: int = _DEFAULT_TTL_MINUTES,
    **kwargs: Any,
) -> dict[str, Any]:
    """Записать значение в рабочую память."""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user_id не определён"}

    if not key or not value:
        return {"ok": False, "error": "key и value обязательны"}

    key = key.strip()[:64]
    expires_at = (
        datetime.now(UTC) + timedelta(minutes=max(1, ttl_minutes))
        if ttl_minutes > 0
        else None
    )

    try:
        async with get_session() as session:
            # Upsert: обновить существующую запись или создать новую
            result = await session.execute(
                select(WorkingMemory).where(
                    WorkingMemory.user_id == user_id,
                    WorkingMemory.key == key,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                existing.value = value
                existing.expires_at = expires_at
            else:
                entry = WorkingMemory(
                    user_id=user_id,
                    key=key,
                    value=value,
                    expires_at=expires_at,
                )
                session.add(entry)
        return {"ok": True, "key": key, "written": True}
    except Exception:
        logger.exception("write_working_memory failed for key=%r", key)
        return {"ok": False, "error": "Ошибка записи в рабочую память"}


@tool(
    name="read_memory",
    description=(
        "Читает значение из рабочей памяти по ключу. "
        "Возвращает None если ключ не найден или истёк."
    ),
    category="memory",
    risk="low",
    params={
        "key": "str — ключ для чтения",
    },
)
async def read_working_memory(
    key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Прочитать значение из рабочей памяти."""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user_id не определён"}
    if not key:
        return {"ok": False, "error": "key обязателен"}

    key = key.strip()[:64]
    now = datetime.now(UTC)

    try:
        async with get_session() as session:
            result = await session.execute(
                select(WorkingMemory).where(
                    WorkingMemory.user_id == user_id,
                    WorkingMemory.key == key,
                )
            )
            entry = result.scalar_one_or_none()
            if entry is None:
                return {"ok": True, "key": key, "value": None, "found": False}
            # Проверка TTL
            if entry.expires_at is not None and entry.expires_at < now:
                await session.delete(entry)
                return {"ok": True, "key": key, "value": None, "found": False}
            return {"ok": True, "key": key, "value": entry.value, "found": True}
    except Exception:
        logger.exception("read_working_memory failed for key=%r", key)
        return {"ok": False, "error": "Ошибка чтения из рабочей памяти"}


@tool(
    name="list_memory",
    description=(
        "Выводит список всех записей в рабочей памяти (ключи и значения). "
        "Истёкшие записи не возвращаются."
    ),
    category="memory",
    risk="low",
    params={},
)
async def list_working_memory(
    **kwargs: Any,
) -> dict[str, Any]:
    """Список всех записей рабочей памяти пользователя."""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user_id не определён"}

    now = datetime.now(UTC)

    try:
        async with get_session() as session:
            result = await session.execute(
                select(WorkingMemory).where(
                    WorkingMemory.user_id == user_id,
                )
            )
            entries = result.scalars().all()
            items: list[dict] = []
            for entry in entries:
                # Пропускаем истёкшие (и заодно чистим)
                if entry.expires_at is not None and entry.expires_at < now:
                    await session.delete(entry)
                    continue
                items.append(
                    {
                        "key": entry.key,
                        "value": entry.value,
                        "expires_in": (
                            round((entry.expires_at - now).total_seconds() / 60, 1)
                            if entry.expires_at
                            else None
                        ),
                    }
                )
            return {"ok": True, "items": items, "count": len(items)}
    except Exception:
        logger.exception("list_working_memory failed")
        return {"ok": False, "error": "Ошибка получения списка рабочей памяти"}


@tool(
    name="clear_memory",
    description=(
        "Очищает рабочую память. Если передан key — удаляет конкретную запись, "
        "иначе удаляет все записи пользователя."
    ),
    category="memory",
    risk="high",
    requires_confirmation=True,
    params={
        "key": "str|None — конкретный ключ для удаления (опционально)",
    },
)
async def clear_working_memory(
    key: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Очистить рабочую память: конкретный ключ или всё."""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user_id не определён"}

    try:
        async with get_session() as session:
            if key is not None:
                key = key.strip()[:64]
                result = await session.execute(
                    delete(WorkingMemory).where(
                        WorkingMemory.user_id == user_id,
                        WorkingMemory.key == key,
                    )
                )
                deleted = result.rowcount
                return {"ok": True, "deleted": deleted, "key": key}
            else:
                result = await session.execute(
                    delete(WorkingMemory).where(
                        WorkingMemory.user_id == user_id,
                    )
                )
                deleted = result.rowcount
                return {"ok": True, "deleted": deleted}
    except Exception:
        logger.exception("clear_working_memory failed")
        return {"ok": False, "error": "Ошибка очистки рабочей памяти"}
