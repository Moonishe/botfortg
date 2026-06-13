"""Dreaming Consolidator — ночное закрепление памяти.

Выполняет цикл консолидации:
1. Отбирает кандидатов из эпизодов и недавних фактов
2. Генерирует контрфактуалы через LLM («а что если бы?..»)
3. Абстрагирует паттерны из повторяющихся ситуаций
4. Интегрирует надёжные паттерны в процедурную память
5. Генерирует инсайты и рекомендации
6. Выполняет forgetting sweep — удаляет малоценные факты

Интегрируется в dream_cycle как Phase 11.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Data Classes
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class ConsolidationCandidate:
    """Кандидат на ночную консолидацию — эпизод или группа фактов.

    Attributes:
        id: Уникальный идентификатор кандидата.
        memory_ids: Список memory.id, относящихся к кандидату.
        summary: Краткое описание контекста.
        created_at: Время создания кандидата.
        importance: Средняя importance входящих фактов.
    """

    id: str
    memory_ids: list[int]
    summary: str
    created_at: datetime
    importance: float = 0.5


@dataclass
class AbstractedPattern:
    """Абстрагированный паттерн — повторяющаяся ситуация или правило.

    Attributes:
        id: Уникальный идентификатор.
        description: Описание паттерна.
        confidence: Уверенность в паттерне (0.0–1.0).
        source_episodes: Количество эпизодов, породивших паттерн.
        category: Категория (behavioral, preference, situational, relational).
    """

    id: str
    description: str
    confidence: float
    source_episodes: int
    category: str = "behavioral"


@dataclass
class Insight:
    """Инсайт — рекомендация или наблюдение для пользователя.

    Attributes:
        id: Уникальный идентификатор.
        text: Текст инсайта.
        confidence: Уверенность (0.0–1.0).
        actionable: Можно ли применить немедленно.
        category: Категория (habit, productivity, relationship, health, learning).
    """

    id: str
    text: str
    confidence: float
    actionable: bool = False
    category: str = "productivity"


# ══════════════════════════════════════════════════════════════════════════
# Dreaming Consolidator
# ══════════════════════════════════════════════════════════════════════════


class DreamingConsolidator:
    """Ночной цикл закрепления памяти: контрфактуалы, паттерны, инсайты.

    Выполняется раз в сутки (в dream_cycle). Обрабатывает эпизоды,
    извлекает паттерны, генерирует инсайты и чистит малоценные факты.
    """

    # ── Конфигурация ──
    MAX_CANDIDATES: int = 10  # макс. кандидатов за цикл
    MAX_COUNTERFACTUALS: int = 3  # макс. контрфактуалов на кандидата
    PATTERN_CONFIDENCE_THRESHOLD: float = 0.6  # мин. confidence для интеграции
    MAX_INSIGHTS: int = 5  # макс. инсайтов за цикл
    FORGET_IMPORTANCE_THRESHOLD: float = 0.1  # порог importance для забывания
    FORGET_MAX_PER_CYCLE: int = 20  # макс. фактов для забывания за цикл

    async def nightly_cycle(self, user_id: int, session, user) -> dict[str, Any]:
        """Выполнить полный ночной цикл консолидации.

        Args:
            user_id: Telegram user_id владельца.
            session: Активная SQLAlchemy сессия.
            user: Объект User (владелец).

        Returns:
            Словарь с результатами:
                {"candidates": int, "counterfactuals": int, "patterns": int,
                 "integrated": int, "insights": int, "forgotten": int,
                 "errors": int}
        """
        if not settings.dreaming_consolidation_enabled:
            return {
                "candidates": 0,
                "counterfactuals": 0,
                "patterns": 0,
                "integrated": 0,
                "insights": 0,
                "forgotten": 0,
                "errors": 0,
            }

        summary = {
            "candidates": 0,
            "counterfactuals": 0,
            "patterns": 0,
            "integrated": 0,
            "insights": 0,
            "forgotten": 0,
            "errors": 0,
        }

        # ── 1. Отбор кандидатов ──
        try:
            candidates = await self._get_candidates(user_id, session, user)
            summary["candidates"] = len(candidates)
            logger.info(
                "DreamingConsolidator: отобрано %d кандидатов (user=%d)",
                len(candidates),
                user_id,
            )
        except Exception:
            logger.exception("DreamingConsolidator: ошибка отбора кандидатов")
            summary["errors"] += 1
            candidates = []

        if not candidates:
            return summary

        # ── 2. Для каждого кандидата: контрфактуалы ──
        all_patterns: list[AbstractedPattern] = []
        for candidate in candidates:
            try:
                # 2a. Генерация контрфактуалов
                counterfactuals = await self._generate_counterfactuals(
                    candidate, session, user
                )
                summary["counterfactuals"] += len(counterfactuals)

                # 2b. Абстракция паттернов
                patterns = await self._abstract_patterns(
                    candidate, counterfactuals, session, user
                )
                summary["patterns"] += len(patterns)
                all_patterns.extend(patterns)

            except Exception:
                logger.exception(
                    "DreamingConsolidator: ошибка обработки кандидата %r",
                    candidate.id,
                )
                summary["errors"] += 1

        # ── 3. Интеграция паттернов в процедурную память ──
        for pattern in all_patterns:
            if pattern.confidence >= self.PATTERN_CONFIDENCE_THRESHOLD:
                try:
                    integrated = await self._integrate_pattern(pattern, user)
                    if integrated:
                        summary["integrated"] += 1
                except Exception:
                    logger.exception(
                        "DreamingConsolidator: ошибка интеграции паттерна %r",
                        pattern.id,
                    )
                    summary["errors"] += 1

        # ── 4. Генерация инсайтов ──
        try:
            insights = await self._generate_insights(user_id, candidates, session, user)
            stored = await self._store_insights(insights, user)
            summary["insights"] = stored
        except Exception:
            logger.exception("DreamingConsolidator: ошибка генерации инсайтов")
            summary["errors"] += 1

        # ── 4.5. Каузальные инсайты (Causal Reasoning) ──
        # Генерирует 2-3 инсайта на основе причинно-следственного анализа
        # эпизодов и цепочек эволюции. Даёт proactive понимание изменений.
        try:
            causal_stored = await self._generate_causal_insights(user_id, session, user)
            summary["insights"] = summary.get("insights", 0) + causal_stored
            if causal_stored:
                logger.info(
                    "DreamingConsolidator: каузальные инсайты — %d сохранено (user=%d)",
                    causal_stored,
                    user_id,
                )
        except Exception:
            logger.exception("DreamingConsolidator: ошибка каузальных инсайтов")
            summary["errors"] += 1

        # ── 5. Forgetting sweep ──
        try:
            forgotten = await self._forgetting_sweep(user_id, session, user)
            summary["forgotten"] = forgotten
            if forgotten:
                logger.info(
                    "DreamingConsolidator: forgetting sweep — %d фактов (user=%d)",
                    forgotten,
                    user_id,
                )
        except Exception:
            logger.exception("DreamingConsolidator: ошибка forgetting sweep")
            summary["errors"] += 1

        return summary

    # ── Private Methods ─────────────────────────────────────────────────

    async def _get_candidates(
        self, user_id: int, session, user
    ) -> list[ConsolidationCandidate]:
        """Отобрать кандидатов для консолидации.

        Критерии:
        - Недавние активные факты (is_active=True, created_at за последние 7 дней)
        - Сгруппированы по cluster_topic
        - Приоритет: самые важные (importance DESC)
        """
        from datetime import timedelta

        from sqlalchemy import desc, select

        from src.db.models._memory import Memory

        cutoff_date = datetime.now(UTC) - timedelta(days=7)

        # Получаем группы по cluster_topic
        result = await session.execute(
            select(
                Memory.cluster_topic,
                Memory.id,
                Memory.fact,
                Memory.importance,
                Memory.created_at,
            )
            .where(
                Memory.user_id == user.id,
                Memory.is_active == True,
                Memory.created_at >= cutoff_date,
            )
            .order_by(desc(Memory.importance))
            .limit(50)
        )
        rows = result.fetchall()

        # Группируем по topic
        groups: dict[str, list] = {}
        for row in rows:
            topic = row[0] or "unsorted"
            if topic not in groups:
                groups[topic] = []
            groups[topic].append(row)

        candidates: list[ConsolidationCandidate] = []
        for topic, mem_rows in groups.items():
            if len(mem_rows) < 2:
                continue  # минимум 2 факта для консолидации

            memory_ids = [r[1] for r in mem_rows]
            avg_importance = sum(r[3] for r in mem_rows) / len(mem_rows)
            summary_text = f"Topic: {topic}, фактов: {len(mem_rows)}"
            # Добавляем выдержки из фактов
            excerpts = [r[2][:80] for r in mem_rows[:3]]
            if excerpts:
                summary_text += f" | {', '.join(excerpts)}"

            candidates.append(
                ConsolidationCandidate(
                    id=f"cand_{topic}_{len(candidates)}",
                    memory_ids=memory_ids,
                    summary=summary_text,
                    created_at=mem_rows[0][4] or datetime.now(UTC),
                    importance=round(avg_importance, 3),
                )
            )

        # Сортируем по importance и берём топ-N
        candidates.sort(key=lambda c: c.importance, reverse=True)
        return candidates[: self.MAX_CANDIDATES]

    async def _generate_counterfactuals(
        self,
        candidate: ConsolidationCandidate,
        session,
        user,
    ) -> list[str]:
        """Сгенерировать контрфактуалы для кандидата через LLM.

        Контрфактуал — гипотетическая альтернативная ситуация:
        «а что если бы пользователь поступил иначе?»

        Возвращает список текстовых контрфактуалов.
        """
        from src.llm.router import build_provider
        from src.llm.base import ChatMessage, TaskType

        provider = await build_provider(session, user, task_type=TaskType.BACKGROUND)
        if not provider:
            logger.debug("DreamingConsolidator: нет LLM для контрфактуалов")
            return []

        prompt = (
            f"📋 **Контекст для анализа:**\n{candidate.summary}\n\n"
            "Сгенерируй до {max_cf} контрфактуальных сценариев — "
            "«а что если бы всё пошло иначе?». "
            "Это гипотетические альтернативы, которые помогают "
            "лучше понять ситуацию и выявить скрытые паттерны.\n\n"
            "Каждый контрфактуал — одна строка, начинающаяся с «• ».\n"
            "Отвечай на русском языке, кратко и содержательно."
        ).format(max_cf=self.MAX_COUNTERFACTUALS)

        try:
            response = await asyncio.wait_for(
                provider.chat(
                    [ChatMessage(role="user", content=prompt)],
                    task_type=TaskType.BACKGROUND,
                ),
                timeout=60.0,
            )
            # Парсим строки, начинающиеся с «• »
            lines = response.strip().split("\n")
            counterfactuals = [
                line.lstrip("• ").strip()
                for line in lines
                if line.strip().startswith("•")
            ]
            return counterfactuals[: self.MAX_COUNTERFACTUALS]
        except Exception:
            logger.exception("DreamingConsolidator: ошибка LLM контрфактуалов")
            return []

    async def _abstract_patterns(
        self,
        candidate: ConsolidationCandidate,
        counterfactuals: list[str],
        session,
        user,
    ) -> list[AbstractedPattern]:
        """Абстрагировать паттерны из эпизода и контрфактуалов.

        Ищет повторяющиеся структуры: поведенческие шаблоны,
        предпочтения, ситуационные правила.

        Возвращает список AbstractedPattern.
        """
        from src.llm.router import build_provider
        from src.llm.base import ChatMessage, TaskType

        provider = await build_provider(session, user, task_type=TaskType.BACKGROUND)
        if not provider:
            logger.debug("DreamingConsolidator: нет LLM для паттернов")
            return []

        cf_block = ""
        if counterfactuals:
            cf_block = (
                "**Контрфактуалы:**\n"
                + "\n".join(f"• {cf}" for cf in counterfactuals)
                + "\n\n"
            )

        prompt = (
            f"📋 **Эпизод:**\n{candidate.summary}\n\n"
            f"{cf_block}"
            "Проанализируй этот эпизод и выдели повторяющиеся паттерны "
            "(до 3 штук). Паттерны могут быть:\n"
            "- **behavioral** — поведенческие шаблоны\n"
            "- **preference** — предпочтения и вкусы\n"
            "- **situational** — повторяющиеся ситуации\n"
            "- **relational** — шаблоны в отношениях\n\n"
            "Формат ответа (строго):\n"
            "PATTERN: <категория> | confidence=<0.0-1.0> | <описание>\n"
            "По одному паттерну на строку.\n"
            "Отвечай на русском языке."
        )

        try:
            response = await asyncio.wait_for(
                provider.chat(
                    [ChatMessage(role="user", content=prompt)],
                    task_type=TaskType.BACKGROUND,
                ),
                timeout=60.0,
            )
        except Exception:
            logger.exception("DreamingConsolidator: ошибка LLM паттернов")
            return []

        if not response:
            return []

        patterns: list[AbstractedPattern] = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line.upper().startswith("PATTERN:"):
                continue

            # Парсим: PATTERN: category | confidence=X.XX | description
            try:
                body = line[len("PATTERN:") :].strip()
                parts = body.split("|")
                if len(parts) < 2:
                    continue

                category = parts[0].strip().lower()
                conf_part = parts[1].strip()
                description = parts[2].strip() if len(parts) > 2 else ""

                # Извлекаем confidence
                confidence = 0.5
                if "confidence=" in conf_part.lower():
                    conf_str = conf_part.lower().split("confidence=")[-1].strip()
                    try:
                        confidence = float(conf_str.split()[0])
                    except ValueError:
                        pass

                if description:
                    patterns.append(
                        AbstractedPattern(
                            id=f"pat_{hash(description) & 0x7FFFFFFF:08x}",
                            description=description,
                            confidence=min(1.0, max(0.0, confidence)),
                            source_episodes=1,
                            category=category,
                        )
                    )
            except Exception:
                logger.debug(
                    "DreamingConsolidator: не удалось распарсить строку: %r", line
                )

        return patterns

    async def _integrate_pattern(self, pattern: AbstractedPattern, user) -> bool:
        """Интегрировать надёжный паттерн в процедурную память.

        Сохраняет паттерн как memory-факт типа 'preference' с высоким confidence.
        """
        from sqlalchemy import select

        from src.db.models._memory import Memory

        # Сохраняем как новый memory-факт
        try:
            from src.db.session import get_session

            async with get_session() as session:
                # Проверяем, нет ли уже похожего факта
                from src.db.repo import get_or_create_user

                owner = await get_or_create_user(session, user.telegram_id)
                if owner is None:
                    return False

                # Проверка на дубликат (по первым 100 символам)
                prefix = (
                    pattern.description[:100].replace("%", "\\%").replace("_", "\\_")
                )
                result = await session.execute(
                    select(Memory.id).where(
                        Memory.user_id == owner.id,
                        Memory.memory_type == "preference",
                        Memory.fact.like(f"%{prefix}%"),
                        Memory.is_active == True,
                    )
                )
                if result.scalars().first():
                    logger.debug(
                        "DreamingConsolidator: паттерн уже существует — пропускаем"
                    )
                    return False

                memory = Memory(
                    user_id=owner.id,
                    fact=f"[Dreaming] {pattern.description}",
                    memory_type="preference",
                    source="auto",
                    confidence=pattern.confidence,
                    importance=pattern.confidence,
                    source_quality=0.7,  # auto-generated, но с проверкой
                    cluster_topic=f"pattern_{pattern.category}",
                    tags=f"dreaming,{pattern.category}",
                    memory_tier=2,  # недельное
                )
                session.add(memory)
                await session.commit()
                logger.info(
                    "DreamingConsolidator: интегрирован паттерн %r (conf=%.2f)",
                    pattern.id,
                    pattern.confidence,
                )
                return True
        except Exception:
            logger.exception("DreamingConsolidator: ошибка интеграции паттерна в БД")
            return False

    async def _generate_insights(
        self,
        user_id: int,
        candidates: list[ConsolidationCandidate],
        session,
        user,
    ) -> list[Insight]:
        """Сгенерировать инсайты на основе всех кандидатов.

        Инсайт — actionable рекомендация или наблюдение для пользователя.
        """
        from src.llm.router import build_provider
        from src.llm.base import ChatMessage, TaskType

        provider = await build_provider(session, user, task_type=TaskType.BACKGROUND)
        if not provider:
            logger.debug("DreamingConsolidator: нет LLM для инсайтов")
            return []

        # Собираем сводку по кандидатам
        summaries = "\n".join(
            f"{i + 1}. {c.summary}" for i, c in enumerate(candidates[:5])
        )

        prompt = (
            "📊 **Анализ эпизодов за период:**\n"
            f"{summaries}\n\n"
            "На основе этих эпизодов сгенерируй до {max_ins} инсайтов — "
            "наблюдений или рекомендаций для пользователя. Инсайты должны быть "
            "конкретными, полезными и actionable.\n\n"
            "Формат ответа (строго):\n"
            "INSIGHT: <категория> | confidence=<0.0-1.0> | actionable=<yes/no> | <текст>\n"
            "По одному инсайту на строку.\n\n"
            "Категории: habit, productivity, relationship, health, learning.\n"
            "Отвечай на русском языке."
        ).format(max_ins=self.MAX_INSIGHTS)

        try:
            response = await asyncio.wait_for(
                provider.chat(
                    [ChatMessage(role="user", content=prompt)],
                    task_type=TaskType.BACKGROUND,
                ),
                timeout=60.0,
            )
        except Exception:
            logger.exception("DreamingConsolidator: ошибка LLM инсайтов")
            return []

        if not response:
            return []

        insights: list[Insight] = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line.upper().startswith("INSIGHT:"):
                continue

            try:
                body = line[len("INSIGHT:") :].strip()
                parts = body.split("|")
                if len(parts) < 3:
                    continue

                category = parts[0].strip().lower()
                conf_part = parts[1].strip()
                action_part = parts[2].strip()
                text = parts[3].strip() if len(parts) > 3 else ""

                confidence = 0.5
                if "confidence=" in conf_part.lower():
                    conf_str = conf_part.lower().split("confidence=")[-1].strip()
                    try:
                        confidence = float(conf_str.split()[0])
                    except ValueError:
                        pass

                actionable = "yes" in action_part.lower()

                if text:
                    insights.append(
                        Insight(
                            id=f"ins_{hash(text) & 0x7FFFFFFF:08x}",
                            text=text,
                            confidence=min(1.0, max(0.0, confidence)),
                            actionable=actionable,
                            category=category,
                        )
                    )
            except Exception:
                logger.debug(
                    "DreamingConsolidator: не удалось распарсить инсайт: %r", line
                )

        return insights

    async def _generate_causal_insights(self, user_id: int, session, user) -> int:
        """Генерирует 2-3 каузальных инсайта на основе причинно-следственного анализа.

        Использует analyze_causes из causal.py для проактивного понимания
        изменений в поведении пользователя. Сохраняет результат как memory-факты
        типа 'insight'.

        Возвращает количество сохранённых инсайтов.
        """
        try:
            from src.core.reasoning.causal import analyze_causes
        except ImportError:
            logger.debug("DreamingConsolidator: causal module not available")
            return 0

        # Список вопросов для каузального анализа
        causal_questions = [
            "Что изменилось в поведении пользователя за последнюю неделю?",
            "Какие ключевые события повлияли на настроение пользователя?",
            "Есть ли признаки выгорания или перегрузки у пользователя?",
        ]

        stored = 0
        for question in causal_questions[:2]:  # берём 2 вопроса для экономии токенов
            try:
                answer = await analyze_causes(
                    user_id=user_id,
                    question=question,
                    session=session,
                    user=user,
                )

                # Пропускаем fallback-ответы (без LLM)
                if not answer or answer.startswith("📊 **Сводка данных"):
                    continue

                # Сохраняем как memory-факт типа insight
                from src.db.session import get_session
                from src.db.models._memory import Memory
                from src.db.repo import get_or_create_user
                from sqlalchemy import select

                async with get_session() as s:
                    owner = await get_or_create_user(s, user.telegram_id)
                    if owner is None:
                        continue

                    # Дедупликация по первым 80 символам
                    prefix = answer[:80].replace("%", "\\%").replace("_", "\\_")
                    result = await s.execute(
                        select(Memory.id).where(
                            Memory.user_id == owner.id,
                            Memory.memory_type == "insight",
                            Memory.fact.like(f"%{prefix}%"),
                            Memory.is_active == True,
                        )
                    )
                    if result.scalars().first():
                        continue

                    memory = Memory(
                        user_id=owner.id,
                        fact=f"[Causal Insight] {answer}",
                        memory_type="insight",
                        source="auto",
                        confidence=0.55,  # умеренная уверенность для авто-анализа
                        importance=0.6,  # выше среднего — proactive понимание
                        source_quality=0.5,
                        cluster_topic="causal_insight",
                        tags="dreaming,causal,insight",
                        memory_tier=2,
                    )
                    s.add(memory)
                    await s.commit()
                    stored += 1

            except Exception:
                logger.debug(
                    "DreamingConsolidator: ошибка каузального инсайта для вопроса %r",
                    question,
                    exc_info=True,
                )

        return stored

    async def _store_insights(self, insights: list[Insight], user) -> int:
        """Сохранить инсайты в БД как memory-факты и отправить в notification_queue.

        Возвращает количество сохранённых инсайтов.
        """
        stored = 0
        for insight in insights:
            try:
                # Сохраняем как memory-факт
                from src.db.session import get_session
                from src.db.models._memory import Memory
                from src.db.repo import get_or_create_user
                from sqlalchemy import select

                async with get_session() as session:
                    owner = await get_or_create_user(session, user.telegram_id)
                    if owner is None:
                        continue

                    # Дедупликация
                    prefix = insight.text[:100]
                    result = await session.execute(
                        select(Memory.id).where(
                            Memory.user_id == owner.id,
                            Memory.memory_type == "insight",
                            Memory.fact.like(f"%{prefix}%"),
                            Memory.is_active == True,
                        )
                    )
                    if result.scalars().first():
                        continue

                    memory = Memory(
                        user_id=owner.id,
                        fact=f"[Insight] {insight.text}",
                        memory_type="insight",
                        source="auto",
                        confidence=insight.confidence,
                        importance=insight.confidence,
                        source_quality=0.65,
                        cluster_topic=f"insight_{insight.category}",
                        tags=f"dreaming,insight,{insight.category}",
                        memory_tier=2,
                    )
                    session.add(memory)
                    await session.commit()
                    stored += 1

                # Отправляем actionable инсайты как уведомления
                if insight.actionable and insight.confidence >= 0.6:
                    from src.core.scheduling.notification_queue import (
                        notification_queue,
                    )

                    await notification_queue.enqueue(
                        topic="insight",
                        text=f"💡 {insight.text}",
                        priority=5,  # PRIORITY_NORMAL
                    )

            except Exception:
                logger.exception(
                    "DreamingConsolidator: ошибка сохранения инсайта %r",
                    insight.id,
                )

        if stored:
            logger.info("DreamingConsolidator: сохранено %d инсайтов", stored)
        return stored

    async def _forgetting_sweep(self, user_id: int, session, user) -> int:
        """Forgetting sweep — деактивировать малоценные факты.

        Критерии:
        - importance < FORGET_IMPORTANCE_THRESHOLD
        - is_active = True
        - created_at старше 7 дней (чтобы не удалять свежее)
        - pinned = False (не удаляем закреплённые)
        """
        from datetime import timedelta

        from sqlalchemy import select, update

        from src.db.models._memory import Memory

        cutoff_date = datetime.now(UTC) - timedelta(days=7)

        result = await session.execute(
            select(Memory.id, Memory.fact)
            .where(
                Memory.user_id == user.id,
                Memory.is_active == True,
                Memory.importance < self.FORGET_IMPORTANCE_THRESHOLD,
                Memory.created_at < cutoff_date,
                Memory.pinned == False,
            )
            .limit(self.FORGET_MAX_PER_CYCLE)
        )
        rows = result.fetchall()

        if not rows:
            return 0

        ids_to_forget = [r[0] for r in rows]

        await session.execute(
            update(Memory).where(Memory.id.in_(ids_to_forget)).values(is_active=False)
        )
        await session.commit()

        return len(ids_to_forget)


# ── Глобальный singleton ─────────────────────────────────────────────
dreaming_consolidator = DreamingConsolidator()
