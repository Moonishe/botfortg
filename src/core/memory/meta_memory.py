"""Meta-Memory: confidence/importance scoring для фактов памяти.

Позволяет боту знать, насколько он уверен в каждом факте.
Не считает одноразовое упоминание равным факту, подтверждённому 5 раз.

Основные функции:
  - calculate_importance — композитный скор важности факта (0–1)
  - boost_confidence — повысить уверенность при подтверждении
  - reduce_confidence — снизить уверенность при противоречии
  - recalculate_all_importance — ночной пересчёт всех фактов
  - get_high_confidence_facts — факты, в которых бот уверен
  - get_low_confidence_facts — факты для верификации
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, UTC

from sqlalchemy import func, select, update as sa_update

from src.config import settings
from src.db.models._memory import Memory
from src.db.session import get_session

logger = logging.getLogger(__name__)


def calculate_importance(
    memory: Memory,
    *,
    reference_time: datetime | None = None,
) -> float:
    """Композитный скор важности факта (0.0–1.0).

    Формула:
        importance = confidence * 0.3
                   + source_quality * 0.2
                   + corroboration_bonus * 0.3
                   + time_decay * 0.2

    corroboration_bonus = min(corroboration_count / 5.0, 1.0)
    time_decay = max(0.1, 1.0 - age_days / 365.0)

    Возвращает значение 0.0–1.0, где 1.0 = максимальная важность.
    """
    now = reference_time or datetime.now(UTC)

    # Уверенность (0.0–1.0)
    confidence = 0.5 if (c := getattr(memory, "confidence", None)) is None else float(c)

    # Качество источника (0.0–1.0)
    source_quality = (
        0.5 if (s := getattr(memory, "source_quality", None)) is None else float(s)
    )

    # Бонус за подтверждения: каждый corroboration даёт +0.2 до максимума 1.0
    corr_count = int(getattr(memory, "corroboration_count", None) or 0)
    corroboration_bonus = min(corr_count / 5.0, 1.0)

    # Временной decay: факт теряет важность со временем
    updated = getattr(memory, "updated_at", None)
    created = getattr(memory, "created_at", None)
    ref_date = updated or created
    if ref_date is not None:
        age_days = (now - ref_date).days
    else:
        age_days = 0.0
    time_decay = max(0.1, 1.0 - age_days / 365.0)

    # Композитный скор
    importance = (
        confidence * 0.3
        + source_quality * 0.2
        + corroboration_bonus * 0.3
        + time_decay * 0.2
    )
    return min(importance, 1.0)


async def boost_confidence(
    memory_id: int,
    user_id: int,
    amount: float | None = None,
    reason: str = "corroboration",
) -> bool:
    """Повышает confidence факта при подтверждении.

    Также инкрементирует corroboration_count и обновляет last_corroborated_at.
    Операция применяется только к фактам, принадлежащим указанному пользователю.

    Args:
        memory_id: ID факта в БД.
        user_id: Внутренний ID владельца факта.
        amount: Величина повышения (по умолчанию из конфига).
        reason: Причина повышения (для логов).

    Returns:
        True если обновление успешно, False иначе.
    """
    boost = amount if amount is not None else settings.meta_memory_confidence_boost
    if not math.isfinite(boost) or boost <= 0:
        logger.warning(
            "boost_confidence: amount must be finite and > 0, got %r - skipping",
            boost,
        )
        return False
    now = datetime.now(UTC)

    try:
        async with get_session() as session:
            result = await session.execute(
                sa_update(Memory)
                .where(Memory.id == memory_id, Memory.user_id == user_id)
                .values(
                    confidence=func.least(1.0, Memory.confidence + boost),
                    corroboration_count=Memory.corroboration_count + 1,
                    last_corroborated_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
            if result.rowcount and result.rowcount > 0:
                # Пересчитываем importance после изменения confidence
                mem = await session.get(Memory, memory_id)
                if mem:
                    new_importance = calculate_importance(mem, reference_time=now)
                    await session.execute(
                        sa_update(Memory)
                        .where(Memory.id == memory_id)
                        .values(importance=min(new_importance, 1.0))
                    )
                    await session.commit()
                logger.debug(
                    "boost_confidence(memory_id=%d, +%.2f, reason=%r)",
                    memory_id,
                    boost,
                    reason,
                )
                return True
    except Exception:
        logger.exception("boost_confidence(memory_id=%d) failed", memory_id)
    return False


async def reduce_confidence(
    memory_id: int,
    user_id: int,
    amount: float | None = None,
    reason: str = "contradiction",
) -> bool:
    """Снижает confidence факта при противоречии.

    Args:
        memory_id: ID факта в БД.
        user_id: Внутренний ID владельца факта.
        amount: Величина снижения (по умолчанию из конфига).
        reason: Причина снижения (для логов).

    Returns:
        True если обновление успешно, False иначе.
    """
    decay = amount if amount is not None else settings.meta_memory_confidence_decay
    if not math.isfinite(decay) or decay <= 0:
        logger.warning(
            "reduce_confidence: amount must be finite and > 0, got %r - skipping",
            decay,
        )
        return False
    now = datetime.now(UTC)

    try:
        async with get_session() as session:
            # Снижаем confidence, но не ниже 0.0, только для факта владельца
            result = await session.execute(
                sa_update(Memory)
                .where(Memory.id == memory_id, Memory.user_id == user_id)
                .values(
                    confidence=func.greatest(0.0, Memory.confidence - decay),
                    updated_at=now,
                )
            )
            await session.commit()
            if result.rowcount and result.rowcount > 0:
                # Пересчитываем importance после изменения confidence
                mem = await session.get(Memory, memory_id)
                if mem:
                    new_importance = calculate_importance(mem, reference_time=now)
                    await session.execute(
                        sa_update(Memory)
                        .where(Memory.id == memory_id)
                        .values(importance=min(new_importance, 1.0))
                    )
                    await session.commit()
                logger.debug(
                    "reduce_confidence(memory_id=%d, -%.2f, reason=%r)",
                    memory_id,
                    decay,
                    reason,
                )
                return True
    except Exception:
        logger.exception("reduce_confidence(memory_id=%d) failed", memory_id)
    return False


async def recalculate_all_importance(
    user_id: int,
) -> int:
    """Ночной пересчёт importance для всех активных фактов пользователя.

    Проходит по всем активным фактам, вычисляет calculate_importance,
    обновляет поле importance в БД.

    Args:
        user_id: Внутренний ID пользователя (user.id, не telegram_id).

    Returns:
        Количество обновлённых фактов.
    """
    now = datetime.now(UTC)
    updated = 0

    try:
        async with get_session() as session:
            result = await session.execute(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.is_active.is_(True),
                )
            )
            memories: list[Memory] = list(result.scalars().all())

            for mem in memories:
                new_importance = calculate_importance(mem, reference_time=now)
                # Округляем до 4 знаков, чтобы не писать без необходимости
                new_importance = round(new_importance, 4)
                if abs((mem.importance or 0.5) - new_importance) > 0.001:
                    mem.importance = new_importance
                    updated += 1

            if updated:
                await session.commit()
            logger.info(
                "recalculate_all_importance(user_id=%d): %d/%d обновлено",
                user_id,
                updated,
                len(memories),
            )
    except Exception:
        logger.exception("recalculate_all_importance(user_id=%d) failed", user_id)
    return updated


async def get_high_confidence_facts(
    user_id: int,
    min_confidence: float = 0.7,
) -> list[Memory]:
    """Возвращает факты, в которых бот уверен (confidence >= min_confidence).

    Args:
        user_id: Внутренний ID пользователя.
        min_confidence: Минимальный порог уверенности (0.0–1.0).

    Returns:
        Список Memory-объектов с высокой уверенностью.
    """
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.is_active.is_(True),
                    Memory.confidence >= min_confidence,
                )
                .order_by(Memory.confidence.desc())
            )
            return list(result.scalars().all())
    except Exception:
        logger.exception("get_high_confidence_facts(user_id=%d) failed", user_id)
        return []


async def get_low_confidence_facts(
    user_id: int,
    max_confidence: float = 0.3,
) -> list[Memory]:
    """Возвращает факты, требующие верификации (confidence <= max_confidence).

    Бот может проактивно спросить пользователя об этих фактах.

    Args:
        user_id: Внутренний ID пользователя.
        max_confidence: Максимальный порог уверенности (0.0–1.0).

    Returns:
        Список Memory-объектов с низкой уверенностью.
    """
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.is_active.is_(True),
                    Memory.confidence <= max_confidence,
                )
                .order_by(Memory.confidence.asc())
            )
            return list(result.scalars().all())
    except Exception:
        logger.exception("get_low_confidence_facts(user_id=%d) failed", user_id)
        return []
