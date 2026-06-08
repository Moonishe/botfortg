"""Preference Learner — обучение на implicit/explicit сигналах пользователя.

Обрабатывает сигналы: accept (подтверждение), reject (отклонение),
edit (коррекция), ignore (игнорирование) — и корректирует важность
связанных фактов в памяти.

Принцип работы:
- accept → boost_related: повышает importance связанных фактов
- reject → decay_related: снижает importance связанных фактов
- edit → adapt_from_correction: адаптирует факт под исправление пользователя

Интеграция: smart_correction → PreferenceLearner.learn()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config import settings
from src.db.session import get_session
from src.db.repo import get_or_create_user

logger = logging.getLogger(__name__)

# ── In-memory history ─────────────────────────────────────────────────
# {telegram_id: [(signal_type, context_hash, timestamp), ...]}
_signal_history: dict[int, list[tuple[str, str, float]]] = {}
_signal_lock = asyncio.Lock()
_MAX_HISTORY = 100


class PreferenceLearner:
    """Простое обучение предпочтениям из implicit/explicit сигналов.

    Не требует отдельного ML-пайплайна — корректирует importance
    существующих фактов на основе обратной связи пользователя.
    """

    # ── Факторы коррекции ──
    ACCEPT_BOOST: float = 1.2  # множитель importance при accept
    REJECT_DECAY: float = 0.8  # множитель importance при reject
    EDIT_ADAPT_FACTOR: float = 1.1  # множитель confidence при edit
    IGNORE_DECAY: float = 0.95  # мягкое снижение при ignore
    MAX_IMPORTANCE: float = 1.0  # потолок importance
    MIN_IMPORTANCE: float = 0.05  # пол importance

    async def learn(
        self,
        signal_type: str,
        context: dict,
        user_id: int,
    ) -> dict[str, Any]:
        """Обработать сигнал обратной связи.

        Args:
            signal_type: Тип сигнала — "accept", "reject", "edit", "ignore".
            context: Контекст сигнала (содержит memory_ids, topic, факты).
            user_id: Telegram user_id владельца.

        Returns:
            Словарь с результатами: {"updated": int, "signal": str, "errors": int}.
        """
        if not settings.preference_learning_enabled:
            return {"updated": 0, "signal": signal_type, "errors": 0}

        # Запись в историю (in-memory)
        context_hash = str(hash(frozenset(context.items()))) if context else "empty"
        async with _signal_lock:
            if user_id not in _signal_history:
                _signal_history[user_id] = []
            _signal_history[user_id].append(
                (signal_type, context_hash, asyncio.get_event_loop().time())
            )
            if len(_signal_history[user_id]) > _MAX_HISTORY:
                _signal_history[user_id].pop(0)

        logger.debug(
            "PreferenceLearner: signal=%s user=%d context_keys=%s",
            signal_type,
            user_id,
            list(context.keys())[:5] if context else [],
        )

        if signal_type == "accept":
            return await self._boost_related(context, user_id)
        elif signal_type == "reject":
            return await self._decay_related(context, user_id)
        elif signal_type == "edit":
            return await self._adapt_from_correction(context, user_id)
        elif signal_type == "ignore":
            return await self._decay_related(context, user_id, factor=self.IGNORE_DECAY)
        else:
            logger.warning("PreferenceLearner: неизвестный тип сигнала %r", signal_type)
            return {"updated": 0, "signal": signal_type, "errors": 1}

    async def _boost_related(
        self, context: dict, user_id: int, factor: float | None = None
    ) -> dict[str, Any]:
        """Повысить importance фактов, связанных с принятым действием.

        Ищет memory_ids в context и повышает их importance на factor.
        """
        boost = factor if factor is not None else self.ACCEPT_BOOST
        memory_ids = self._extract_memory_ids(context)
        if not memory_ids:
            # Fallback: ищем по topic
            return await self._boost_by_topic(context, user_id, boost)

        return await self._adjust_importance(memory_ids, user_id, boost)

    async def _decay_related(
        self, context: dict, user_id: int, factor: float | None = None
    ) -> dict[str, Any]:
        """Снизить importance фактов, связанных с отклонённым действием."""
        decay = factor if factor is not None else self.REJECT_DECAY
        memory_ids = self._extract_memory_ids(context)
        if not memory_ids:
            return await self._decay_by_topic(context, user_id, decay)

        return await self._adjust_importance(memory_ids, user_id, decay)

    async def _adapt_from_correction(
        self, context: dict, user_id: int
    ) -> dict[str, Any]:
        """Адаптировать факт под корректировку пользователя.

        Повышает confidence факта (пользователь уточнил — значит факт важен)
        и немного повышает importance.
        """
        memory_ids = self._extract_memory_ids(context)
        if not memory_ids:
            logger.debug("PreferenceLearner: edit без memory_ids — пропускаем")
            return {"updated": 0, "signal": "edit", "errors": 0}

        updated = 0
        errors = 0
        async with get_session() as session:
            owner = await get_or_create_user(session, user_id)
            if owner is None:
                return {"updated": 0, "signal": "edit", "errors": 1}

            from sqlalchemy import select

            from src.db.models._memory import Memory

            for mid in memory_ids:
                try:
                    result = await session.execute(
                        select(Memory).where(
                            Memory.id == mid,
                            Memory.user_id == owner.id,
                            Memory.is_active == True,
                        )
                    )
                    memory = result.scalar_one_or_none()
                    if memory is None:
                        continue

                    # Повышаем confidence — пользователь взаимодействовал
                    memory.confidence = min(
                        1.0, memory.confidence * self.EDIT_ADAPT_FACTOR
                    )
                    memory.importance = min(
                        self.MAX_IMPORTANCE,
                        memory.importance * self.EDIT_ADAPT_FACTOR,
                    )
                    memory.times_mentioned += 1
                    updated += 1
                except Exception:
                    logger.exception(
                        "PreferenceLearner: ошибка адаптации memory_id=%d", mid
                    )
                    errors += 1

            if updated:
                await session.commit()
                logger.info(
                    "PreferenceLearner: edit — адаптировано %d фактов (user=%d)",
                    updated,
                    user_id,
                )

        return {"updated": updated, "signal": "edit", "errors": errors}

    async def _adjust_importance(
        self, memory_ids: list[int], user_id: int, factor: float
    ) -> dict[str, Any]:
        """Массовая корректировка importance для списка memory_ids."""
        updated = 0
        errors = 0
        async with get_session() as session:
            owner = await get_or_create_user(session, user_id)
            if owner is None:
                return {"updated": 0, "signal": "adjust", "errors": 1}

            from sqlalchemy import select

            from src.db.models._memory import Memory

            for mid in memory_ids:
                try:
                    result = await session.execute(
                        select(Memory).where(
                            Memory.id == mid,
                            Memory.user_id == owner.id,
                            Memory.is_active == True,
                        )
                    )
                    memory = result.scalar_one_or_none()
                    if memory is None:
                        continue

                    new_importance = memory.importance * factor
                    memory.importance = max(
                        self.MIN_IMPORTANCE,
                        min(self.MAX_IMPORTANCE, new_importance),
                    )
                    updated += 1
                except Exception:
                    logger.exception("PreferenceLearner: ошибка для memory_id=%d", mid)
                    errors += 1

            if updated:
                await session.commit()
                logger.info(
                    "PreferenceLearner: factor=%.2f — скорректировано %d фактов (user=%d)",
                    factor,
                    updated,
                    user_id,
                )

        return {"updated": updated, "signal": "adjust", "errors": errors}

    async def _boost_by_topic(
        self, context: dict, user_id: int, factor: float
    ) -> dict[str, Any]:
        """Повысить importance фактов по topic (когда нет прямых memory_ids)."""
        topic = context.get("topic") or context.get("category")
        if not topic:
            return {"updated": 0, "signal": "boost_topic", "errors": 0}

        updated = 0
        errors = 0
        async with get_session() as session:
            owner = await get_or_create_user(session, user_id)
            if owner is None:
                return {"updated": 0, "signal": "boost_topic", "errors": 1}

            from sqlalchemy import select, update

            from src.db.models._memory import Memory

            # Находим факты с matching cluster_topic или tags
            result = await session.execute(
                select(Memory.id).where(
                    Memory.user_id == owner.id,
                    Memory.is_active == True,
                    (
                        Memory.cluster_topic.ilike(f"%{topic}%")
                        | Memory.tags.ilike(f"%{topic}%")
                    ),
                )
            )
            matching_ids = [row[0] for row in result.fetchall()]

            for mid in matching_ids[:20]:  # ограничиваем чтобы не задеть слишком много
                try:
                    await session.execute(
                        update(Memory)
                        .where(Memory.id == mid)
                        .values(
                            importance=min(
                                self.MAX_IMPORTANCE,
                                Memory.importance * factor,
                            )
                        )
                    )
                    updated += 1
                except Exception:
                    errors += 1

            if updated:
                await session.commit()
                logger.info(
                    "PreferenceLearner: boost по topic=%r — %d фактов (user=%d)",
                    topic,
                    updated,
                    user_id,
                )

        return {"updated": updated, "signal": "boost_topic", "errors": errors}

    async def _decay_by_topic(
        self, context: dict, user_id: int, factor: float
    ) -> dict[str, Any]:
        """Снизить importance фактов по topic (аналогично boost)."""
        topic = context.get("topic") or context.get("category")
        if not topic:
            return {"updated": 0, "signal": "decay_topic", "errors": 0}

        updated = 0
        errors = 0
        async with get_session() as session:
            owner = await get_or_create_user(session, user_id)
            if owner is None:
                return {"updated": 0, "signal": "decay_topic", "errors": 1}

            from sqlalchemy import select, update

            from src.db.models._memory import Memory

            result = await session.execute(
                select(Memory.id).where(
                    Memory.user_id == owner.id,
                    Memory.is_active == True,
                    (
                        Memory.cluster_topic.ilike(f"%{topic}%")
                        | Memory.tags.ilike(f"%{topic}%")
                    ),
                )
            )
            matching_ids = [row[0] for row in result.fetchall()]

            for mid in matching_ids[:20]:
                try:
                    await session.execute(
                        update(Memory)
                        .where(Memory.id == mid)
                        .values(
                            importance=max(
                                self.MIN_IMPORTANCE,
                                Memory.importance * factor,
                            )
                        )
                    )
                    updated += 1
                except Exception:
                    errors += 1

            if updated:
                await session.commit()
                logger.info(
                    "PreferenceLearner: decay по topic=%r — %d фактов (user=%d)",
                    topic,
                    updated,
                    user_id,
                )

        return {"updated": updated, "signal": "decay_topic", "errors": errors}

    @staticmethod
    def _extract_memory_ids(context: dict) -> list[int]:
        """Извлечь memory_ids из контекста."""
        ids = context.get("memory_ids", [])
        if isinstance(ids, list):
            return [
                int(i) for i in ids if isinstance(i, (int, str)) and str(i).isdigit()
            ]
        return []

    @classmethod
    async def get_signal_stats(cls, user_id: int) -> dict:
        """Получить статистику сигналов для пользователя."""
        async with _signal_lock:
            history = _signal_history.get(user_id, [])
        counts: dict[str, int] = {}
        for sig_type, _, _ in history:
            counts[sig_type] = counts.get(sig_type, 0) + 1
        return {
            "total_signals": len(history),
            "by_type": counts,
        }


# ── Глобальный singleton ─────────────────────────────────────────────
preference_learner = PreferenceLearner()
