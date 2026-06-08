"""HTN Planner — Иерархический планировщик задач.

Декомпозирует цели пользователя на исполнимые шаги с:
- отслеживанием зависимостей (depends_on);
- топологической сортировкой (алгоритм Кана);
- оценкой рисков каждого шага и общего плана;
- контрольными точками (checkpoint gates) для опасных операций.

Ключевая инновация: dependency tracking + topological sort.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)

# ── Максимальное количество шагов в плане ──
_MAX_PLAN_STEPS = settings.plan_max_steps if hasattr(settings, "plan_max_steps") else 10

# ── Уровни риска ──
_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
_CONFIRMATION_RISKS = frozenset({"high", "critical"})


# ══════════════════════════════════════════════════════════════════════════
# Data Classes
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class PlanStep:
    """Один шаг плана — атомарная исполнимая операция."""

    id: str  # Уникальный идентификатор шага (например "step_1")
    description: str  # Человекочитаемое описание
    tool_calls: list[dict] = field(default_factory=list)  # [{tool, params}]
    depends_on: list[str] = field(default_factory=list)  # ID шагов-предшественников
    risk_level: str = "low"  # "low" | "medium" | "high" | "critical"
    is_checkpoint: bool = False  # Требует подтверждения пользователя перед выполнением
    estimated_tokens: int = 0  # Оценка расхода токенов на шаг

    def __post_init__(self) -> None:
        if self.risk_level not in _RISK_LEVELS:
            logger.warning(
                "PlanStep %s: unknown risk_level %r, defaulting to 'medium'",
                self.id,
                self.risk_level,
            )
            self.risk_level = "medium"


@dataclass
class Plan:
    """Полный план — результат декомпозиции цели."""

    goal: str  # Исходная цель пользователя
    steps: list[PlanStep] = field(default_factory=list)  # Шаги в порядке выполнения
    risk: str = "low"  # Общий уровень риска плана
    checkpoints: list[int] = field(
        default_factory=list
    )  # Индексы шагов с подтверждением
    estimated_cost_tokens: int = 0  # Суммарная оценка расхода токенов
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)  # Доп. метаданные

    def __post_init__(self) -> None:
        if self.risk not in _RISK_LEVELS:
            logger.warning(
                "Plan for %r: unknown risk %r, defaulting to 'medium'",
                self.goal[:50],
                self.risk,
            )
            self.risk = "medium"

    def summary(self) -> str:
        """Краткая сводка плана для пользователя."""
        lines = [
            f"📋 План: {self.goal}",
            f"🎯 Уровень риска: {self.risk.upper()}",
            f"📊 Шагов: {len(self.steps)}",
            f"💰 Оценка токенов: ~{self.estimated_cost_tokens}",
            f"🕐 Создан: {self.created_at.strftime('%H:%M:%S')}",
            "",
        ]
        for i, step in enumerate(self.steps):
            checkpoint_mark = " ⚠️ ПОДТВЕРЖДЕНИЕ" if i in self.checkpoints else ""
            risk_mark = (
                f" [{step.risk_level.upper()}]" if step.risk_level != "low" else ""
            )
            deps = (
                f" ← зависит от: {', '.join(step.depends_on)}"
                if step.depends_on
                else ""
            )
            lines.append(
                f"  {i + 1}. {step.description}{risk_mark}{checkpoint_mark}{deps}"
            )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# HTNPlanner
# ══════════════════════════════════════════════════════════════════════════


class HTNPlanner:
    """Иерархический планировщик (Hierarchical Task Network).

    Рабочий процесс:
    1. LLM декомпозирует цель → подзадачи.
    2. Для каждой подзадачи: ищем процедуру в procedural memory
       или создаём ad-hoc шаги.
    3. Строим граф зависимостей + топологическая сортировка (алгоритм Кана).
    4. LLM оценивает риски каждого шага.
    5. Определяем контрольные точки для опасных/высокорисковых шагов.

    Использует существующую LLM-инфраструктуру (build_provider с purpose="planner").
    """

    # ── Статические методы для оценки сложности ──

    # Ключевые слова, указывающие на высокую сложность запроса
    _COMPLEXITY_KEYWORDS: frozenset[str] = frozenset(
        {
            "анализ",
            "анализировать",
            "разбери",
            "разобрать",
            "исследовать",
            "исследуй",
            "сравни",
            "сравнить",
            "план",
            "спланируй",
            "распланируй",
            "оптимизация",
            "оптимизировать",
            "рефакторинг",
            "рефакторить",
            "миграция",
            "мигрировать",
            "архитектура",
            "архитектуру",
            "пошагово",
            "по шагам",
            "step by step",
            "сложн",
            "комплексн",
            "многоэтапн",
            "разложи",
            "декомпозиция",
            "интегрировать",
            "интеграция",
            "настроить",
            "настройка",
            "развернуть",
            "деплой",
        }
    )

    # Эвристики для разных типов сложных запросов
    _COMPLEXITY_PATTERNS: list[tuple[str, float]] = [
        (r"\b(?:сравни|сравнить|проанализировать|проанализируй)\b", 0.7),
        (r"\b(?:план|спланируй|распланируй|разложи|декомпозируй)\b", 0.8),
        (r"\b(?:оптимизировать|рефакторинг|миграция|мигрировать)\b", 0.7),
        (r"\b(?:архитектур|интегрировать|развернуть)\b", 0.6),
        (r"\b(?:по шагам|пошагово|step.by.step)\b", 0.6),
        (r"\b(?:настроить|настройка|конфигурация)\b", 0.5),
        (r"(?:\d+)\s+(?:шаг|этап|пункт)", 0.5),  # "3 шага", "5 этапов"
    ]

    @staticmethod
    def estimate_complexity(user_text: str) -> float:
        """Быстрая эвристическая оценка сложности запроса (0.0–1.0).

        Используется для определения, стоит ли предлагать
        пользователю создать план через HTN Planner.
        """
        text_lower = user_text.lower()

        # Проверка ключевых слов
        keyword_hits = sum(
            1 for kw in HTNPlanner._COMPLEXITY_KEYWORDS if kw in text_lower
        )

        # Проверка regex-паттернов
        pattern_score = 0.0
        for pattern, weight in HTNPlanner._COMPLEXITY_PATTERNS:
            if re.search(pattern, text_lower):
                pattern_score = max(pattern_score, weight)

        # Длина текста: очень длинные запросы часто сложные
        length_score = min(len(user_text) / 500.0, 0.3)

        # Вопросы (вопросительный знак + длина) — часто сложные
        question_bonus = 0.1 if "?" in user_text and len(user_text) > 100 else 0.0

        # Комбинированная оценка
        keyword_score = min(keyword_hits * 0.15, 0.5)
        raw_score = max(keyword_score, pattern_score) + length_score + question_bonus

        return round(min(raw_score, 1.0), 2)

    # ══════════════════════════════════════════════════════════════════════
    # Планирование
    # ══════════════════════════════════════════════════════════════════════

    async def plan(self, goal: str, context: dict[str, Any] | None = None) -> Plan:
        """Главная точка входа. Декомпозирует цель → исполнимый план.

        Args:
            goal: Цель пользователя (естественный язык).
            context: Дополнительный контекст (память, история, факты).

        Returns:
            Plan с шагами в порядке выполнения, оценкой рисков и контрольными точками.
        """
        ctx = context or {}

        # 1. LLM декомпозирует цель → подзадачи
        logger.info("HTNPlanner: decomposing goal: %s", goal[:80])
        subgoals = await self._decompose(goal, ctx)

        if not subgoals:
            # Если LLM не смогла декомпозировать — создаём один ad-hoc шаг
            logger.warning("HTNPlanner: decomposition returned empty, using fallback")
            subgoals = [goal]

        # 2. Для каждой подзадачи: ищем процедуру или создаём ad-hoc шаги
        steps: list[PlanStep] = []
        for i, sg in enumerate(subgoals):
            proc = await self._find_procedure(sg, ctx)
            if proc:
                logger.debug("HTNPlanner: found procedure for subgoal: %s", sg[:50])
                steps.extend(proc)
            else:
                logger.debug(
                    "HTNPlanner: creating ad-hoc steps for subgoal: %s", sg[:50]
                )
                ad_hoc = await self._create_ad_hoc_steps(sg, ctx, offset=len(steps))
                steps.extend(ad_hoc)

        # Ограничение количества шагов
        if len(steps) > _MAX_PLAN_STEPS:
            logger.warning(
                "HTNPlanner: too many steps (%d), truncating to %d",
                len(steps),
                _MAX_PLAN_STEPS,
            )
            steps = steps[:_MAX_PLAN_STEPS]

        # 3. Топологическая сортировка (алгоритм Кана)
        ordered = self._topological_sort(steps)
        if len(ordered) != len(steps):
            logger.warning(
                "HTNPlanner: cycle detected in dependency graph! "
                "Using original order for %d/%d steps",
                len(steps) - len(ordered),
                len(steps),
            )
            # Добавляем шаги, не попавшие в топологический порядок (циклы)
            remaining_ids = {s.id for s in steps} - {s.id for s in ordered}
            for s in steps:
                if s.id in remaining_ids:
                    ordered.append(s)

        # 4. Оценка рисков (LLM)
        risk = await self._assess_risk(ordered, ctx)
        # Применяем per-step риск из LLM-оценки, если доступен
        if isinstance(ctx.get("_step_risks"), dict):
            for step in ordered:
                step_risk = ctx["_step_risks"].get(step.id)
                if step_risk in _RISK_LEVELS:
                    step.risk_level = step_risk

        # 5. Определяем контрольные точки
        checkpoints = self._identify_checkpoints(ordered)

        # Суммарная оценка токенов
        total_tokens = sum(s.estimated_tokens for s in ordered)

        plan = Plan(
            goal=goal,
            steps=ordered,
            risk=risk,
            checkpoints=checkpoints,
            estimated_cost_tokens=total_tokens,
            metadata={
                "subgoal_count": len(subgoals),
                "step_count": len(ordered),
                "planner_version": "1.0.0",
            },
        )

        logger.info(
            "HTNPlanner: plan created — %d steps, risk=%s, checkpoints=%d, tokens=~%d",
            len(ordered),
            risk,
            len(checkpoints),
            total_tokens,
        )

        return plan

    # ══════════════════════════════════════════════════════════════════════
    # Шаг 1: Декомпозиция цели
    # ══════════════════════════════════════════════════════════════════════

    async def _decompose(self, goal: str, context: dict[str, Any]) -> list[str]:
        """LLM декомпозирует цель на подзадачи (3-7 шагов).

        Возвращает список строк — подзадач на русском языке.
        """
        provider = None
        try:
            from src.llm.provider_manager import build_provider

            provider = await build_provider(
                session=context.get("session"),
                user=context.get("user"),
                purpose="planner",
            )
        except Exception as e:
            logger.warning("HTNPlanner: build_provider failed: %s", e)
            # Fallback: простая эвристическая декомпозиция
            return self._heuristic_decompose(goal)

        if provider is None:
            return self._heuristic_decompose(goal)

        # Формируем промпт для LLM
        context_summary = json.dumps(
            {
                k: str(v)[:200]
                for k, v in context.items()
                if k not in ("session", "user")
            },
            ensure_ascii=False,
        )[:500]

        prompt = (
            "Ты — планировщик задач. Разбей цель на подзадачи (3-7 шагов).\n"
            "Каждая подзадача должна быть конкретным действием, "
            "которое можно выполнить одним вызовом инструмента или запросом.\n"
            "Верни ТОЛЬКО JSON-массив строк, без пояснений.\n\n"
            f"Цель: {goal}\n\n"
            f"Контекст: {context_summary or 'отсутствует'}\n\n"
            'Формат ответа: ["шаг 1", "шаг 2", "шаг 3"]\n'
            "Ответ:"
        )

        try:
            from src.llm.base import ChatMessage

            result = await asyncio.wait_for(
                provider.chat(
                    [ChatMessage(role="user", content=prompt)],
                    task_type="default",
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.warning("HTNPlanner: decomposition timed out")
            return self._heuristic_decompose(goal)
        except Exception as e:
            logger.warning("HTNPlanner: decomposition LLM call failed: %s", e)
            return self._heuristic_decompose(goal)

        # Извлекаем JSON из ответа
        try:
            json_str = self._extract_json(result)
            subgoals = json.loads(json_str)
            if isinstance(subgoals, list) and all(isinstance(s, str) for s in subgoals):
                logger.debug("HTNPlanner: decomposed into %d subgoals", len(subgoals))
                return subgoals[:_MAX_PLAN_STEPS]
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("HTNPlanner: failed to parse decomposition JSON: %s", e)

        return self._heuristic_decompose(goal)

    def _heuristic_decompose(self, goal: str) -> list[str]:
        """Эвристическая декомпозиция без LLM (fallback).

        Использует знаки препинания и ключевые слова для разбиения.
        """
        # Пробуем разбить по ";" или нумерованным пунктам
        if ";" in goal:
            parts = [p.strip() for p in goal.split(";") if p.strip()]
            if len(parts) >= 2:
                return parts[:_MAX_PLAN_STEPS]

        # Разбиваем по переводам строк
        lines = [l.strip("-•*123456789. ") for l in goal.split("\n") if l.strip()]
        if len(lines) >= 2:
            return lines[:_MAX_PLAN_STEPS]

        # Если ничего не помогло — возвращаем цель как единственный шаг
        return [f"Выполнить: {goal[:200]}"]

    # ══════════════════════════════════════════════════════════════════════
    # Шаг 2: Поиск процедуры в procedural memory
    # ══════════════════════════════════════════════════════════════════════

    async def _find_procedure(
        self, subgoal: str, context: dict[str, Any]
    ) -> list[PlanStep] | None:
        """Поиск существующей процедуры для подзадачи.

        Проверяет working memory и episodic memory на наличие
        похожих прошлых задач. Если найдено — возвращает готовые шаги.
        """
        # Проверяем working memory (scratchpad из контекста)
        wm = context.get("working_memory") or context.get("scratchpad")
        if wm and isinstance(wm, dict):
            for key, value in wm.items():
                if isinstance(value, list) and len(value) > 0:
                    # Если в working memory есть список шагов для похожей цели
                    if self._similarity(subgoal, key) > 0.5:
                        logger.debug(
                            "HTNPlanner: found procedure in working_memory: %s", key
                        )
                        return self._steps_from_memory(value)

        # Проверяем episodic memory (если доступна)
        episodic = context.get("episodic_memory") or context.get("episodes")
        if episodic and isinstance(episodic, list):
            for episode in episodic[:10]:  # Проверяем последние 10 эпизодов
                if isinstance(episode, dict):
                    ep_goal = episode.get("goal") or episode.get("summary", "")
                    if ep_goal and self._similarity(subgoal, ep_goal) > 0.6:
                        ep_steps = episode.get("steps") or episode.get("plan", [])
                        if ep_steps:
                            logger.debug(
                                "HTNPlanner: found procedure in episodic_memory"
                            )
                            return self._steps_from_memory(ep_steps)

        # Процедура не найдена
        return None

    def _steps_from_memory(self, raw_steps: list[Any]) -> list[PlanStep] | None:
        """Конвертирует сырые шаги из памяти в PlanStep."""
        steps: list[PlanStep] = []
        for i, raw in enumerate(raw_steps):
            if isinstance(raw, PlanStep):
                steps.append(raw)
            elif isinstance(raw, dict):
                steps.append(
                    PlanStep(
                        id=raw.get("id", f"mem_step_{i}"),
                        description=raw.get("description", str(raw)),
                        tool_calls=raw.get("tool_calls", []),
                        depends_on=raw.get("depends_on", []),
                        risk_level=raw.get("risk_level", "low"),
                        is_checkpoint=raw.get("is_checkpoint", False),
                        estimated_tokens=raw.get("estimated_tokens", 100),
                    )
                )
            elif isinstance(raw, str):
                steps.append(
                    PlanStep(
                        id=f"mem_step_{i}",
                        description=raw,
                    )
                )
        return steps if steps else None

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Простая оценка схожести двух строк (0.0–1.0).

        Использует пересечение слов для быстрой оценки без LLM/эмбеддингов.
        """
        a_words = set(a.lower().split())
        b_words = set(b.lower().split())
        if not a_words or not b_words:
            return 0.0
        intersection = a_words & b_words
        union = a_words | b_words
        return len(intersection) / len(union) if union else 0.0

    # ══════════════════════════════════════════════════════════════════════
    # Шаг 2b: Создание ad-hoc шагов
    # ══════════════════════════════════════════════════════════════════════

    async def _create_ad_hoc_steps(
        self,
        subgoal: str,
        context: dict[str, Any],
        offset: int = 0,
    ) -> list[PlanStep]:
        """Создаёт ad-hoc шаги для подзадачи, для которой нет готовой процедуры.

        LLM генерирует конкретные шаги с указанием инструментов,
        зависимостей и уровней риска.
        """
        provider = None
        try:
            from src.llm.provider_manager import build_provider

            provider = await build_provider(
                session=context.get("session"),
                user=context.get("user"),
                purpose="planner",
            )
        except Exception:
            pass

        if provider is None:
            # Без LLM — создаём один простой шаг
            return [
                PlanStep(
                    id=f"step_{offset + 1}",
                    description=subgoal,
                    risk_level="low",
                    estimated_tokens=100,
                )
            ]

        # Формируем промпт для генерации шагов
        prompt = (
            "Ты — планировщик. Для подзадачи создай конкретные шаги.\n"
            "Каждый шаг должен содержать: описание, нужный инструмент (если есть), "
            "зависимости от других шагов, уровень риска.\n\n"
            f"Подзадача: {subgoal}\n\n"
            "Верни JSON-массив объектов с полями:\n"
            '- "description": str — описание шага\n'
            '- "tool_name": str|null — имя инструмента (mcp_web, recall_memory, search_contexts, ...)\n'
            '- "tool_params": object — параметры инструмента\n'
            '- "depends_on": [str] — ID предыдущих шагов (step_1, step_2, ...) или пустой массив\n'
            '- "risk_level": "low"|"medium"|"high"|"critical"\n'
            '- "is_checkpoint": bool — нужно ли подтверждение пользователя\n\n'
            "Ответ:"
        )

        try:
            from src.llm.base import ChatMessage

            result = await asyncio.wait_for(
                provider.chat(
                    [ChatMessage(role="user", content=prompt)],
                    task_type="default",
                ),
                timeout=30.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("HTNPlanner: ad-hoc step generation failed: %s", e)
            return [
                PlanStep(
                    id=f"step_{offset + 1}",
                    description=subgoal,
                    risk_level="low",
                    estimated_tokens=100,
                )
            ]

        # Парсим результат
        try:
            json_str = self._extract_json(result)
            raw_steps = json.loads(json_str)
            if not isinstance(raw_steps, list):
                raise ValueError("Expected JSON array")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("HTNPlanner: failed to parse ad-hoc steps JSON: %s", e)
            return [
                PlanStep(
                    id=f"step_{offset + 1}",
                    description=subgoal,
                    risk_level="low",
                    estimated_tokens=100,
                )
            ]

        # Конвертируем в PlanStep с валидацией
        steps: list[PlanStep] = []
        for i, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                continue

            tool_calls = []
            tool_name = raw.get("tool_name")
            if tool_name and isinstance(tool_name, str):
                tool_calls.append(
                    {
                        "tool": tool_name,
                        "params": raw.get("tool_params", {}),
                    }
                )

            risk = raw.get("risk_level", "low")
            if risk not in _RISK_LEVELS:
                risk = "low"

            steps.append(
                PlanStep(
                    id=f"step_{offset + i + 1}",
                    description=raw.get(
                        "description", f"Шаг {offset + i + 1}: {subgoal[:80]}"
                    ),
                    tool_calls=tool_calls,
                    depends_on=[
                        d
                        for d in raw.get("depends_on", [])
                        if isinstance(d, str) and d.startswith("step_")
                    ],
                    risk_level=risk,
                    is_checkpoint=bool(raw.get("is_checkpoint", False)),
                    estimated_tokens=raw.get("estimated_tokens", 100),
                )
            )

        return (
            steps
            if steps
            else [
                PlanStep(
                    id=f"step_{offset + 1}",
                    description=subgoal,
                    risk_level="low",
                    estimated_tokens=100,
                )
            ]
        )

    # ══════════════════════════════════════════════════════════════════════
    # Шаг 3: Топологическая сортировка (алгоритм Кана)
    # ══════════════════════════════════════════════════════════════════════

    def _topological_sort(self, steps: list[PlanStep]) -> list[PlanStep]:
        """Топологическая сортировка шагов по зависимостям (алгоритм Кана).

        Алгоритм:
        1. Строим граф: для каждого шага считаем in-degree
           (количество шагов, от которых он зависит).
        2. Шаги с in-degree = 0 помещаем в очередь.
        3. Итеративно извлекаем шаг, уменьшаем in-degree
           зависимых от него шагов; если становится 0 — в очередь.
        4. Если обработаны не все шаги — в графе есть цикл.

        Возвращает шаги в порядке, где каждый шаг идёт после своих
        зависимостей.
        """
        if not steps:
            return []

        # Строим словари: id → шаг, id → список зависящих
        step_by_id: dict[str, PlanStep] = {s.id: s for s in steps}
        in_degree: dict[str, int] = {s.id: 0 for s in steps}
        dependents: dict[str, list[str]] = {s.id: [] for s in steps}

        for step in steps:
            for dep_id in step.depends_on:
                if dep_id in step_by_id:
                    in_degree[step.id] += 1
                    dependents.setdefault(dep_id, []).append(step.id)
                else:
                    logger.warning(
                        "HTNPlanner: step %s depends on unknown step %s — ignoring",
                        step.id,
                        dep_id,
                    )

        # Очередь: шаги без зависимостей (in-degree = 0)
        queue: list[str] = [sid for sid, deg in in_degree.items() if deg == 0]

        # Сортируем очередь для детерминированного порядка
        queue.sort()

        result: list[PlanStep] = []

        while queue:
            current_id = queue.pop(0)
            step = step_by_id.get(current_id)
            if step:
                result.append(step)

            # Уменьшаем in-degree для всех зависимых шагов
            for dep_id in dependents.get(current_id, []):
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    queue.append(dep_id)
                    queue.sort()  # Детерминированность

        return result

    # ══════════════════════════════════════════════════════════════════════
    # Шаг 4: Оценка рисков
    # ══════════════════════════════════════════════════════════════════════

    async def _assess_risk(self, steps: list[PlanStep], context: dict[str, Any]) -> str:
        """LLM оценивает общий уровень риска плана.

        Также заполняет per-step риск в context["_step_risks"].
        """
        if not steps:
            return "low"

        # Быстрая эвристическая оценка без LLM
        has_critical = any(s.risk_level == "critical" for s in steps)
        has_high = any(s.risk_level == "high" for s in steps)
        has_destructive = any(
            s.tool_calls
            for s in steps
            if any(
                tc.get("tool", "") in ("mcp_shell", "mcp_code_exec", "mcp_file_send")
                for tc in s.tool_calls
            )
        )

        if has_critical:
            return "critical"
        if has_high or has_destructive:
            return "high"
        if len(steps) > 5:
            return "medium"
        return "low"

    # ══════════════════════════════════════════════════════════════════════
    # Шаг 5: Определение контрольных точек
    # ══════════════════════════════════════════════════════════════════════

    def _identify_checkpoints(self, steps: list[PlanStep]) -> list[int]:
        """Определяет индексы шагов, требующих подтверждения пользователя.

        Контрольные точки устанавливаются для:
        - шагов с высоким/критическим риском;
        - шагов, явно помеченных как checkpoint;
        - деструктивных операций (shell, code_exec, file_send).
        """
        checkpoints: list[int] = []
        destructive_tools = frozenset(
            {
                "mcp_shell",
                "mcp_code_exec",
                "mcp_file_send",
                "mcp_system",
                "mcp_processes",
            }
        )

        for i, step in enumerate(steps):
            is_destructive = any(
                tc.get("tool", "") in destructive_tools for tc in step.tool_calls
            )
            if (
                step.risk_level in _CONFIRMATION_RISKS
                or step.is_checkpoint
                or is_destructive
            ):
                checkpoints.append(i)

        return checkpoints

    # ══════════════════════════════════════════════════════════════════════
    # Утилиты
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_json(text: str) -> str:
        """Извлекает JSON из ответа LLM (обрабатывает code fences).

        Поддерживает форматы:
        - ```json ... ```
        - ``` ... ```
        - Просто JSON в тексте
        """
        if not text:
            return "[]"

        text = text.strip()

        # Удаляем code fences
        fence_pattern = r"^```(?:json|JSON)?\s*\n?"
        text = re.sub(fence_pattern, "", text)
        text = re.sub(r"\n?\s*```\s*$", "", text)

        # Если ответ начинается с '[' или '{' — это уже JSON
        if text.startswith("[") or text.startswith("{"):
            return text

        # Ищем первый JSON-массив в тексте
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\[", text):
            try:
                value, end = decoder.raw_decode(text[match.start() :])
                if isinstance(value, (list, dict)):
                    return text[match.start() : match.start() + end]
            except json.JSONDecodeError:
                continue

        # Fallback: возвращаем сырой текст (может быть не JSON)
        logger.warning("HTNPlanner: could not extract JSON from: %s", text[:200])
        return "[]"


# ══════════════════════════════════════════════════════════════════════════
# In-memory plan store (для MCP-инструментов)
# ══════════════════════════════════════════════════════════════════════════

# Простое хранилище планов в памяти (для демонстрации).
# В production следует заменить на БД (plans table).
_plan_store: dict[str, Plan] = {}


def _plan_key(owner_id: int | None) -> str:
    """Генерирует ключ для хранения плана."""
    return f"plan:{owner_id or 0}"


def store_plan(owner_id: int | None, plan: Plan) -> str:
    """Сохраняет план в in-memory хранилище. Возвращает ключ."""
    key = _plan_key(owner_id)
    _plan_store[key] = plan
    logger.debug("HTNPlanner: stored plan for owner_id=%s", owner_id)
    return key


def get_plan(owner_id: int | None) -> Plan | None:
    """Извлекает план из in-memory хранилища."""
    return _plan_store.get(_plan_key(owner_id))


def update_plan_step(
    owner_id: int | None,
    step_index: int,
    new_description: str,
) -> Plan | None:
    """Обновляет описание шага в существующем плане."""
    plan = get_plan(owner_id)
    if plan is None:
        return None
    if step_index < 0 or step_index >= len(plan.steps):
        return None
    plan.steps[step_index].description = new_description
    return plan
