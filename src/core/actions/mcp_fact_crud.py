"""MCP Tools: CRUD-операции с фактами памяти.

Инструменты:
- ``add_fact`` — создать новый факт в памяти
- ``delete_fact`` — удалить факт из памяти (требует подтверждения)
- ``list_facts`` — список фактов памяти с фильтрацией
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select

from src.core.actions.tool_registry import tool
from src.db.models._memory import Memory
from src.db.repo import get_or_create_user
from src.db.session import get_session

logger = logging.getLogger(__name__)


def _resolve_user_id(kwargs: dict[str, Any]) -> int | None:
    """Извлекает user.id (внутренний первичный ключ) из runtime kwargs."""
    _user_val = kwargs.get("user")
    if _user_val is None:
        return None
    if hasattr(_user_val, "id"):
        return int(_user_val.id)  # type: ignore[union-attr]
    try:
        return int(_user_val)
    except (TypeError, ValueError):
        return None


# ── Инструменты ──────────────────────────────────────────────────────────


@tool(
    name="add_fact",
    description=(
        "Создать новый факт в памяти. Сохраняет информацию о пользователе или контакте "
        "для дальнейшего использования. Поддерживает указание тональности и важности."
    ),
    category="memory",
    risk="low",
    params={
        "fact_text": "str — текст факта для сохранения",
        "sentiment": "str='neutral' — тональность: positive, negative, neutral",
        "importance": "float=0.5 — важность факта от 0.0 до 1.0",
    },
)
async def add_fact(
    fact_text: str,
    sentiment: str = "neutral",
    importance: float = 0.5,
    **kwargs: Any,
) -> dict[str, Any]:
    """Создать новый факт в памяти."""
    user = kwargs.get("user")
    if user is None:
        return {"ok": False, "error": "user is required"}

    if not fact_text or not fact_text.strip():
        return {"ok": False, "error": "fact_text обязателен"}

    # Валидация sentiment
    sentiment = sentiment.strip().lower()
    if sentiment not in ("positive", "negative", "neutral"):
        sentiment = "neutral"

    # Валидация importance
    importance = min(max(float(importance), 0.0), 1.0)

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, user.telegram_id)

            new_memory = Memory(
                user_id=owner.id,
                fact=fact_text.strip(),
                sentiment=sentiment,
                importance=importance,
                source="user",
                source_quality=1.0,
                confidence=1.0,
                memory_type="personal",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(new_memory)
            await session.commit()
            await session.refresh(new_memory)

            logger.info(
                "add_fact: создан факт #%d для user %d",
                new_memory.id,
                user.telegram_id,
            )

            return {
                "ok": True,
                "memory_id": new_memory.id,
                "fact": new_memory.fact,
                "sentiment": new_memory.sentiment,
                "importance": new_memory.importance,
                "created_at": new_memory.created_at.isoformat()
                if new_memory.created_at
                else None,
                "message": f"Факт #{new_memory.id} создан.",
            }

    except Exception:
        logger.exception("add_fact failed")
        return {"ok": False, "error": "Ошибка создания факта"}


@tool(
    name="delete_fact",
    description=(
        "Удалить факт из памяти. Необратимая операция — факт и история его версий "
        "удаляются полностью. Требует подтверждения пользователя."
    ),
    category="memory",
    risk="high",
    requires_confirmation=True,
    params={
        "memory_id": "int — ID факта для удаления",
    },
)
async def delete_fact(
    memory_id: int,
    **kwargs: Any,
) -> dict[str, Any]:
    """Удалить факт из памяти."""
    user = kwargs.get("user")
    if user is None:
        return {"ok": False, "error": "user is required"}

    if memory_id <= 0:
        return {"ok": False, "error": "memory_id must be > 0"}

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, user.telegram_id)

            # Проверяем принадлежность факта
            result = await session.execute(
                select(Memory).where(
                    and_(
                        Memory.id == memory_id,
                        Memory.user_id == owner.id,
                    )
                )
            )
            memory = result.scalar_one_or_none()

            if memory is None:
                return {
                    "ok": False,
                    "error": f"Факт #{memory_id} не найден или не принадлежит вам",
                }

            fact_preview = memory.fact[:100]
            await session.delete(memory)
            await session.commit()

            logger.info(
                "delete_fact: удалён факт #%d для user %d",
                memory_id,
                user.telegram_id,
            )

            return {
                "ok": True,
                "memory_id": memory_id,
                "deleted_fact": fact_preview,
                "message": f"Факт #{memory_id} удалён.",
            }

    except Exception:
        logger.exception("delete_fact failed for memory_id=%d", memory_id)
        return {"ok": False, "error": "Ошибка удаления факта"}


@tool(
    name="list_facts",
    description=(
        "Список фактов памяти с фильтрацией. Возвращает последние факты пользователя. "
        "Можно фильтровать по contact_id и ограничивать количество."
    ),
    category="memory",
    risk="low",
    params={
        "limit": "int=20 — максимальное количество возвращаемых фактов",
        "contact_id": "int|None — фильтр по ID контакта",
    },
)
async def list_facts(
    limit: int = 20,
    contact_id: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Список фактов памяти."""
    user = kwargs.get("user")
    if user is None:
        return {"ok": False, "error": "user is required"}

    limit = max(1, min(limit, 100))

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, user.telegram_id)

            conditions = [
                Memory.user_id == owner.id,
                Memory.is_active == True,  # noqa: E712
            ]
            if contact_id is not None:
                conditions.append(Memory.contact_id == contact_id)

            result = await session.execute(
                select(Memory)
                .where(and_(*conditions))
                .order_by(Memory.created_at.desc())
                .limit(limit)
            )
            memories = result.scalars().all()

            facts_list: list[dict[str, Any]] = []
            for m in memories:
                facts_list.append(
                    {
                        "id": m.id,
                        "fact": m.fact,
                        "sentiment": m.sentiment,
                        "importance": m.importance,
                        "confidence": m.confidence,
                        "source": m.source,
                        "memory_type": m.memory_type,
                        "contact_id": m.contact_id,
                        "is_active": m.is_active,
                        "times_mentioned": m.times_mentioned,
                        "created_at": m.created_at.isoformat()
                        if m.created_at
                        else None,
                    }
                )

            return {
                "ok": True,
                "facts": facts_list,
                "total": len(facts_list),
                "limit": limit,
                "contact_id": contact_id,
            }

    except Exception:
        logger.exception("list_facts failed")
        return {"ok": False, "error": "Ошибка получения списка фактов", "facts": []}
