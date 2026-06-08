"""Meta-Reasoner — слой мета-рассуждений (Phase 2).

Оценивает качество цепочек рассуждений, выявляет пробелы в знаниях,
предлагает делегирование специализированным агентам и решает,
когда нужно спросить пользователя.

Вызывается:
- из Maestro после завершения сложных задач
- из CoT Engine после завершения цепочки рассуждений
- Результат встраивается в контекст для следующих шагов
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.llm.base import ChatMessage
from src.llm.router import build_provider

logger = logging.getLogger(__name__)

# ── Константы умолчаний (переопределяются через config) ──
_DEFAULT_DELEGATION_THRESHOLD = 0.4
_DEFAULT_CONSULT_THRESHOLD = 0.3
_DEFAULT_ABORT_THRESHOLD = 0.1


@dataclass
class MetaEvaluation:
    """Результат мета-оценки рассуждения."""

    confidence: float  # 0-1 общая уверенность в рассуждении
    gaps: list[str]  # выявленные пробелы в знаниях
    assumptions: list[str]  # непроверенные допущения
    alternatives: list[str]  # альтернативные подходы
    should_delegate: bool  # делегировать специализированному агенту?
    delegate_to: str | None  # "researcher" | "coder" | "planner" | "analyst"
    should_ask_user: bool  # спросить пользователя?
    questions_for_user: list[str]  # что спросить
    risk_level: str  # "low" | "medium" | "high" | "critical"
    recommendation: str  # "proceed" | "delegate" | "ask_user" | "abort"


class MetaReasoner:
    """Оценивает качество рассуждений и принимает мета-решения.

    Использует существующий провайдер LLM через build_provider
    для семантического анализа пробелов, допущений и альтернатив.
    Пороги настраиваются через src.config.Settings.

    Usage::

        reasoner = MetaReasoner(session, user)
        eval_result = await reasoner.evaluate(trace_or_plan, context)
        if eval_result.recommendation == "delegate":
            ...
    """

    def __init__(self, session: AsyncSession, user) -> None:
        """Инициализирует MetaReasoner с сессией БД и пользователем.

        Args:
            session: AsyncSession для построения LLM-провайдера.
            user: Объект User (из src.db.models) для аутентификации.
        """
        self._session = session
        self._user = user

    # ── Свойства-пороги (читаются из settings с fallback на умолчания) ──

    @property
    def _delegation_threshold(self) -> float:
        return getattr(
            settings, "meta_delegation_threshold", _DEFAULT_DELEGATION_THRESHOLD
        )

    @property
    def _consult_threshold(self) -> float:
        return getattr(settings, "meta_consult_threshold", _DEFAULT_CONSULT_THRESHOLD)

    @property
    def _abort_threshold(self) -> float:
        return getattr(settings, "meta_abort_threshold", _DEFAULT_ABORT_THRESHOLD)

    # ═══════════════════════════════════════════════════════════════════
    #  Основной метод
    # ═══════════════════════════════════════════════════════════════════

    async def evaluate(self, trace_or_plan, context: dict) -> MetaEvaluation:
        """Главная точка входа. Оценивает trace рассуждения или план.

        Args:
            trace_or_plan: ReasoningTrace (со steps) или объект Plan.
            context: Словарь с контекстом (user_text, history, agent_results…).

        Returns:
            MetaEvaluation с confidence, gaps, рекомендациями.
        """
        # 1. Вычисляем базовую уверенность
        confidence = self._compute_confidence(trace_or_plan)

        # 2. LLM-анализ: пробелы в знаниях
        gaps = await self._identify_gaps(trace_or_plan, context)

        # 3. Извлекаем непроверенные допущения
        assumptions = await self._extract_assumptions(trace_or_plan)

        # 4. Генерируем альтернативные подходы
        alternatives = await self._suggest_alternatives(trace_or_plan, context)

        # 5. Решение о делегировании
        should_delegate, delegate_to = self._decide_delegation(
            confidence, gaps, context
        )

        # 6. Решение о консультации с пользователем
        should_ask, questions = self._decide_consult(confidence, gaps, context)

        # 7. Оценка риска
        risk = self._assess_risk(trace_or_plan, gaps)

        # 8. Итоговая рекомендация
        recommendation = self._make_recommendation(
            confidence, should_ask, should_delegate
        )

        return MetaEvaluation(
            confidence=confidence,
            gaps=gaps,
            assumptions=assumptions,
            alternatives=alternatives,
            should_delegate=should_delegate,
            delegate_to=delegate_to,
            should_ask_user=should_ask,
            questions_for_user=questions,
            risk_level=risk,
            recommendation=recommendation,
        )

    # ═══════════════════════════════════════════════════════════════════
    #  1. Вычисление confidence
    # ═══════════════════════════════════════════════════════════════════

    def _compute_confidence(self, trace_or_plan) -> float:
        """Вычисляет общую уверенность по шагам trace или структуре плана.

        Для ReasoningTrace (со steps) — среднее confidence по шагам.
        Для плана — 0.5 по умолчанию (план ещё не исполнен).
        """
        if hasattr(trace_or_plan, "steps"):
            # ReasoningTrace — усредняем confidence шагов
            confidences = [
                s.confidence for s in trace_or_plan.steps if hasattr(s, "confidence")
            ]
            return sum(confidences) / len(confidences) if confidences else 0.3
        # Plan или неизвестный тип — default
        return 0.5

    # ═══════════════════════════════════════════════════════════════════
    #  2. Выявление пробелов (LLM)
    # ═══════════════════════════════════════════════════════════════════

    async def _identify_gaps(self, trace_or_plan, context: dict) -> list[str]:
        """LLM анализирует рассуждение и находит пробелы в знаниях."""
        return await self._llm_analyze(
            prompt_template=(
                "Проанализируй рассуждение и найди пробелы в знаниях.\n\n"
                "Рассуждение:\n{trace}\n\n"
                "Контекст:\n{ctx}\n\n"
                "Каких знаний не хватает для уверенного ответа? "
                'Верни ТОЛЬКО JSON-массив строк (например: ["gap1", "gap2"]). '
                "Если пробелов нет — верни []."
            ),
            trace_or_plan=trace_or_plan,
            context=context,
            default=[],
        )

    # ═══════════════════════════════════════════════════════════════════
    #  3. Извлечение допущений (LLM)
    # ═══════════════════════════════════════════════════════════════════

    async def _extract_assumptions(self, trace_or_plan) -> list[str]:
        """LLM извлекает непроверенные допущения из рассуждения."""
        return await self._llm_analyze(
            prompt_template=(
                "Проанализируй рассуждение и найди непроверенные допущения.\n\n"
                "Рассуждение:\n{trace}\n\n"
                "Какие допущения не проверены и могут быть ошибочными? "
                "Верни ТОЛЬКО JSON-массив строк. Если допущений нет — верни []."
            ),
            trace_or_plan=trace_or_plan,
            context={},
            default=[],
        )

    # ═══════════════════════════════════════════════════════════════════
    #  4. Генерация альтернатив (LLM)
    # ═══════════════════════════════════════════════════════════════════

    async def _suggest_alternatives(self, trace_or_plan, context: dict) -> list[str]:
        """LLM предлагает альтернативные подходы к решению."""
        return await self._llm_analyze(
            prompt_template=(
                "Проанализируй рассуждение и предложи альтернативные подходы.\n\n"
                "Рассуждение:\n{trace}\n\n"
                "Контекст:\n{ctx}\n\n"
                "Какие есть альтернативные способы решить задачу? "
                "Верни ТОЛЬКО JSON-массив строк. Если альтернатив нет — верни []."
            ),
            trace_or_plan=trace_or_plan,
            context=context,
            default=[],
        )

    # ═══════════════════════════════════════════════════════════════════
    #  Вспомогательный метод LLM-анализа
    # ═══════════════════════════════════════════════════════════════════

    async def _llm_analyze(
        self,
        prompt_template: str,
        trace_or_plan,
        context: dict,
        default: list[str],
    ) -> list[str]:
        """Общий метод LLM-анализа с форматированием промпта и парсингом JSON.

        Args:
            prompt_template: Шаблон промпта с {trace} и {ctx}.
            trace_or_plan: Объект trace или plan для форматирования.
            context: Словарь контекста.
            default: Значение по умолчанию при ошибке.

        Returns:
            Список строк, извлечённых из LLM-ответа.
        """
        trace_str = self._format_trace(trace_or_plan)
        ctx_str = json.dumps(context, ensure_ascii=False, default=str)[:500]
        prompt = prompt_template.format(trace=trace_str, ctx=ctx_str)

        try:
            provider = await build_provider(
                self._session,
                self._user,
                purpose="reasoning",
            )
            if provider is None:
                logger.warning("MetaReasoner._llm_analyze: провайдер не построен")
                return default

            messages = [ChatMessage(role="user", content=prompt)]
            raw = await provider.chat(messages)
            return self._parse_json_list(raw)

        except Exception as exc:
            logger.warning(
                "MetaReasoner._llm_analyze: ошибка LLM-вызова: %s",
                exc,
            )
            return default

    # ═══════════════════════════════════════════════════════════════════
    #  5. Решение о делегировании
    # ═══════════════════════════════════════════════════════════════════

    def _decide_delegation(
        self, confidence: float, gaps: list[str], context: dict
    ) -> tuple[bool, str | None]:
        """Решает, делегировать ли задачу специализированному агенту.

        Делегирование происходит если confidence ниже порога.
        Тип агента определяется по ключевым словам в пробелах.
        """
        if confidence >= self._delegation_threshold:
            return False, None

        # Классифицируем тип необходимой помощи по ключевым словам
        gap_text = " ".join(gaps).lower()

        if any(
            w in gap_text
            for w in ("code", "program", "function", "api", "код", "программ", "функци")
        ):
            return True, "coder"
        if any(
            w in gap_text
            for w in (
                "search",
                "find",
                "research",
                "look up",
                "поиск",
                "найт",
                "исследова",
            )
        ):
            return True, "researcher"
        if any(
            w in gap_text
            for w in ("plan", "schedule", "organize", "steps", "план", "шаг", "организ")
        ):
            return True, "planner"
        if any(
            w in gap_text
            for w in (
                "analyze",
                "data",
                "stats",
                "numbers",
                "анализ",
                "данны",
                "статисти",
            )
        ):
            return True, "analyst"

        return False, None

    # ═══════════════════════════════════════════════════════════════════
    #  6. Решение о консультации с пользователем
    # ═══════════════════════════════════════════════════════════════════

    def _decide_consult(
        self, confidence: float, gaps: list[str], context: dict
    ) -> tuple[bool, list[str]]:
        """Решает, нужно ли спросить пользователя.

        Срабатывает если confidence ниже порога И есть пробелы.
        Формирует вопросы на основе первых трёх пробелов.
        """
        if confidence < self._consult_threshold and gaps:
            questions = [f"Не хватает информации: {g}" for g in gaps[:3]]
            return True, questions
        return False, []

    # ═══════════════════════════════════════════════════════════════════
    #  7. Оценка риска
    # ═══════════════════════════════════════════════════════════════════

    def _assess_risk(self, trace_or_plan, gaps: list[str]) -> str:
        """Оценивает уровень риска на основе количества пробелов и confidence."""
        if not gaps:
            return "low"
        if len(gaps) > 3:
            return "high"
        if len(gaps) > 1:
            return "medium"
        return "low"

    # ═══════════════════════════════════════════════════════════════════
    #  8. Итоговая рекомендация
    # ═══════════════════════════════════════════════════════════════════

    def _make_recommendation(
        self, confidence: float, should_ask: bool, should_delegate: bool
    ) -> str:
        """Формирует итоговую рекомендацию на основе приоритетов.

        Приоритет: abort > ask_user > delegate > proceed.
        """
        if confidence < self._abort_threshold:
            return "abort"
        if should_ask:
            return "ask_user"
        if should_delegate:
            return "delegate"
        return "proceed"

    # ═══════════════════════════════════════════════════════════════════
    #  Утилиты
    # ═══════════════════════════════════════════════════════════════════

    def _format_trace(self, trace_or_plan) -> str:
        """Форматирует trace или план для LLM-анализа.

        Для ReasoningTrace выводит шаги (до 10).
        Для всего остального — str().
        """
        if hasattr(trace_or_plan, "steps"):
            lines = []
            for i, step in enumerate(trace_or_plan.steps[:10]):
                thought = getattr(step, "thought", str(step))
                conf = getattr(step, "confidence", None)
                suffix = f" [confidence={conf:.2f}]" if conf is not None else ""
                lines.append(f"Шаг {i + 1}: {thought}{suffix}")
            return "\n".join(lines)
        return str(trace_or_plan)

    @staticmethod
    def _parse_json_list(raw: str) -> list[str]:
        """Извлекает JSON-массив строк из LLM-ответа.

        Устойчив к markdown-блокам, лишнему тексту до/после JSON.
        """
        # Убираем markdown-блоки ```json ... ```
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()

        # Ищем первый JSON-массив
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if not match:
            logger.debug(
                "MetaReasoner._parse_json_list: JSON-массив не найден в ответе"
            )
            return []

        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list) and all(
                isinstance(item, str) for item in parsed
            ):
                return parsed
            # Если элементы не строки — приводим
            return [str(item) for item in parsed]
        except (json.JSONDecodeError, TypeError) as exc:
            logger.debug("MetaReasoner._parse_json_list: ошибка парсинга JSON: %s", exc)
            return []
