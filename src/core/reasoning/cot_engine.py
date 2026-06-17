"""Chain-of-Thought (CoT) Engine — пошаговые рассуждения с самокоррекцией.

Реализует многошаговое логическое рассуждение: LLM генерирует шаг, вызывает
инструменты, получает наблюдения, исправляет ошибки через петлю самокоррекции.

Фаза 2: добавляет reasoning как надстройку над существующей tool‑loop Maestro.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from src.config import settings
from src.llm.base import ChatMessage, TaskType

logger = logging.getLogger(__name__)

# ── Датаклассы ──────────────────────────────────────────────────────────


@dataclass
class ReasoningStep:
    """Один шаг рассуждения в цепочке."""

    step_number: int
    thought: str  # рассуждение LLM для этого шага
    tool_calls: list[dict] | None = None  # вызванные инструменты
    observations: list[dict] | None = None  # результаты инструментов
    is_correction: bool = False  # шаг самокоррекции?
    confidence: float = 0.5  # 0–1


@dataclass
class ReasoningTrace:
    """Полный трейс рассуждения — все шаги + итог."""

    problem: str
    steps: list[ReasoningStep] = field(default_factory=list)
    final_answer: str | None = None
    total_iterations: int = 0
    total_tool_calls: int = 0
    solved: bool = False


# ── Промпты ─────────────────────────────────────────────────────────────

# Системный промпт для генерации шага: объясняет формат и правила.
_STEP_SYSTEM = """Ты — эксперт по пошаговому решению задач. Думай вслух, шаг за шагом.

Правила:
1. Опиши СЛЕДУЮЩИЙ логический шаг для решения задачи.
2. Если для шага нужны инструменты — укажи их в tool_calls.
3. Если задача уже решена — закончи thought строкой "[FINAL]".
4. Указывай confidence (0.0–1.0): насколько ты уверен в этом шаге.
5. Отвечай СТРОГО в JSON-формате:
   {"thought": "твоё рассуждение", "tool_calls": [...], "confidence": 0.8}

tool_calls — массив объектов: [{"tool": "имя_инструмента", "params": {...}}]
Если инструменты не нужны — tool_calls: []"""

# Промпт для самокоррекции после ошибки инструмента.
_CORRECTION_SYSTEM = """Ты корректируешь подход после неудачного шага.

Предыдущий шаг провалился. Проанализируй ошибки и предложи ИСПРАВЛЕННЫЙ подход.
Не повторяй те же параметры, которые привели к ошибке.
Отвечай СТРОГО в JSON-формате:
{"thought": "исправленное рассуждение", "tool_calls": [...], "confidence": 0.7}"""


def _extract_json(raw: str) -> dict | None:
    """Извлечь первый JSON-объект из ответа модели."""
    decoder = json.JSONDecoder()
    for match in __import__("re").finditer(r"\{", raw):
        try:
            value, _end = decoder.raw_decode(raw[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


# ── CoTEngine ────────────────────────────────────────────────────────────


class CoTEngine:
    """Chain-of-Thought движок с петлёй самокоррекции.

    Использует существующую LLM-инфраструктуру и tool_registry для вызова
    инструментов. Ограничен жёсткими лимитами итераций и вызовов инструментов
    для предотвращения бесконечных циклов.
    """

    # Жёсткие лимиты — предотвращают runaway‑циклы
    MAX_ITERATIONS: int = 8
    MAX_TOOL_CALLS: int = 12

    def __init__(self) -> None:
        # Применяем конфиг, если поля заданы
        self.MAX_ITERATIONS = getattr(settings, "cot_max_iterations", 8)
        self.MAX_TOOL_CALLS = getattr(settings, "cot_max_tool_calls", 12)

    # ── Публичный API ───────────────────────────────────────────────────

    async def reason(
        self,
        problem: str,
        context: dict,
        *,
        session=None,
        user=None,
        plan: dict | None = None,
    ) -> ReasoningTrace:
        """Главная точка входа. Многошаговое рассуждение с инструментами и
самокоррекцией.

        Args:
            problem: задача пользователя (текст).
            context: словарь контекста (факты из памяти, persona, стиль, ...).
            session: SQLAlchemy AsyncSession (для build_provider и тулов).
            user: ORM-объект пользователя.
            plan: опциональный план (словарь с ключом «steps») — scaffolding.

        Returns:
            ReasoningTrace с полной историей шагов и финальным ответом.
        """
        trace = ReasoningTrace(problem=problem)

        # Если передан готовый план — выполняем его как scaffolding
        if plan and plan.get("steps"):
            return await self._execute_plan(plan, context, trace, session, user)

        # Свободное рассуждение: LLM генерирует шаги автономно
        provider = await self._get_provider(session, user)

        for i in range(self.MAX_ITERATIONS):
            trace.total_iterations = i + 1

            # Генерация следующего шага
            step = await self._generate_step(problem, context, trace, provider)
            if step is None:
                logger.warning("CoT: _generate_step вернул None на итерации %d", i)
                break
            step.step_number = i + 1
            trace.steps.append(step)

            # Выполнение инструментов, если есть
            if step.tool_calls:
                observations = await self._execute_tools(
                    step.tool_calls, context, session, user
                )
                step.observations = observations
                trace.total_tool_calls += len(step.tool_calls)

                # Самокоррекция при ошибках
                if self._has_errors(observations):
                    correction = await self._self_correct(
                        step, observations, context, provider
                    )
                    if correction is not None:
                        correction.is_correction = True
                        correction.step_number = i + 1  # тот же номер итерации
                        trace.steps.append(correction)
                        # Выполняем инструменты коррекции
                        if correction.tool_calls:
                            corr_obs = await self._execute_tools(
                                correction.tool_calls, context, session, user
                            )
                            correction.observations = corr_obs
                            trace.total_tool_calls += len(correction.tool_calls)

            # Проверка завершения — учитываем и исходный шаг, и коррекцию
            last_step = trace.steps[-1]
            if last_step.thought.endswith("[FINAL]") or self._is_solved(trace):
                trace.final_answer = last_step.thought
                trace.solved = True
                break

            # Превышен лимит инструментов — принудительный выход
            if trace.total_tool_calls >= self.MAX_TOOL_CALLS:
                logger.warning(
                    "CoT: превышен лимит инструментов (%d/%d)",
                    trace.total_tool_calls,
                    self.MAX_TOOL_CALLS,
                )
                break

        return trace

    # ── Генерация шага ──────────────────────────────────────────────────

    async def _generate_step(
        self,
        problem: str,
        context: dict,
        trace: ReasoningTrace,
        provider,
    ) -> ReasoningStep | None:
        """LLM генерирует следующий шаг рассуждения."""
        ctx_str = json.dumps(context, ensure_ascii=False)[:500]
        trace_str = self._format_trace(trace)

        prompt = (
            f"Задача: {problem}\n\n"
            f"Контекст: {ctx_str}\n\n"
            f"Предыдущие шаги:\n{trace_str}\n\n"
            f"Сгенерируй следующий шаг рассуждения. "
            f"Если задача решена — закончи мысль строкой '[FINAL]' с ответом."
        )

        try:
            messages = [
                ChatMessage(role="system", content=_STEP_SYSTEM),
                ChatMessage(role="user", content=prompt),
            ]
            raw = await asyncio.wait_for(
                provider.chat(messages, task_type=TaskType.DEFAULT),
                timeout=60.0,
            )
        except TimeoutError:
            logger.warning("CoT: таймаут генерации шага")
            return None
        except Exception:
            logger.exception("CoT: ошибка генерации шага")
            return None

        return self._parse_step(raw.strip())

    # ── Самокоррекция ───────────────────────────────────────────────────

    async def _self_correct(
        self,
        failed_step: ReasoningStep,
        errors: list[dict],
        context: dict,
        provider,
    ) -> ReasoningStep | None:
        """Генерация корректирующего шага после ошибки инструмента."""
        # Слишком низкая уверенность — пропускаем коррекцию
        if failed_step.confidence < 0.3:
            logger.info(
                "CoT: пропуск коррекции — confidence=%.2f", failed_step.confidence
            )
            return None

        err_str = json.dumps(errors, ensure_ascii=False)
        ctx_str = json.dumps(context, ensure_ascii=False)[:300]

        prompt = (
            f"Проваленный шаг: {failed_step.thought}\n"
            f"Ошибки: {err_str}\n"
            f"Контекст: {ctx_str}\n\n"
            f"Предложи ИСПРАВЛЕННЫЙ подход. Не повторяй те же параметры."
        )

        try:
            messages = [
                ChatMessage(role="system", content=_CORRECTION_SYSTEM),
                ChatMessage(role="user", content=prompt),
            ]
            raw = await asyncio.wait_for(
                provider.chat(messages, task_type=TaskType.DEFAULT),
                timeout=45.0,
            )
        except TimeoutError:
            logger.warning("CoT: таймаут самокоррекции")
            return None
        except Exception:
            logger.exception("CoT: ошибка самокоррекции")
            return None

        step = self._parse_step(raw.strip())
        if step is not None:
            step.is_correction = True
        return step

    # ── Выполнение инструментов ─────────────────────────────────────────

    async def _execute_tools(
        self,
        tool_calls: list[dict],
        context: dict,
        session,
        user,
    ) -> list[dict]:
        """Выполнить инструменты конкурентно (до 4 параллельно)."""
        from src.core.actions.tool_registry import tool_registry

        sem = asyncio.Semaphore(4)

        async def _exec_one(tc: dict) -> dict:
            tool_name = tc.get("tool", "")
            params = tc.get("params", {})
            async with sem:
                try:
                    # Защита от конфликта ключей: params не должны
                    # переопределять session/user
                    params.pop("session", None)
                    params.pop("user", None)
                    result = await tool_registry.execute(
                        tool_name,
                        _confirmed=False,
                        session=session,
                        user=user,
                        **params,
                    )
                    return result if result is not None else {"ok": True}
                except Exception as exc:
                    logger.warning("CoT: инструмент '%s' упал: %s", tool_name, exc)
                    return {"error": str(exc), "tool": tool_name}

        raw_results = await asyncio.gather(
            *[_exec_one(tc) for tc in tool_calls],
            return_exceptions=True,
        )

        # Нормализация: исключения → dict с error
        results: list[dict] = []
        for r in raw_results:
            if isinstance(r, BaseException):
                results.append({"error": str(r)})
            elif isinstance(r, dict):
                results.append(r)
            else:
                results.append({"ok": True, "result": r})
        return results

    # ── Выполнение готового плана ───────────────────────────────────────

    async def _execute_plan(
        self,
        plan: dict,
        context: dict,
        trace: ReasoningTrace,
        session,
        user,
    ) -> ReasoningTrace:
        """Выполнить шаги готового плана (scaffolding)."""
        provider = await self._get_provider(session, user)

        for i, step_def in enumerate(plan.get("steps", [])):
            if trace.total_iterations >= self.MAX_ITERATIONS:
                break
            if trace.total_tool_calls >= self.MAX_TOOL_CALLS:
                break

            trace.total_iterations += 1
            step = self._plan_step_to_reasoning(step_def, i + 1)
            trace.steps.append(step)

            if step.tool_calls:
                obs = await self._execute_tools(step.tool_calls, context, session, user)
                step.observations = obs
                trace.total_tool_calls += len(step.tool_calls)

                if self._has_errors(obs):
                    correction = await self._self_correct(step, obs, context, provider)
                    if correction:
                        correction.is_correction = True
                        correction.step_number = i + 1
                        trace.steps.append(correction)
                        if correction.tool_calls:
                            corr_obs = await self._execute_tools(
                                correction.tool_calls, context, session, user
                            )
                            correction.observations = corr_obs
                            trace.total_tool_calls += len(correction.tool_calls)

            # Проверка завершения — учитываем и исходный шаг, и коррекцию
            last_step = trace.steps[-1]
            if self._is_solved(trace) or last_step.thought.endswith("[FINAL]"):
                trace.final_answer = last_step.thought
                trace.solved = True
                break

        return trace

    # ── Хелперы ─────────────────────────────────────────────────────────

    async def _get_provider(self, session, user):
        """Собрать LLM-провайдера для reasoning."""
        try:
            from src.llm.router import build_provider

            return await build_provider(
                session, user, purpose="reasoning", task_type=TaskType.DEFAULT
            )
        except Exception:
            logger.exception("CoT: не удалось собрать провайдера")
            raise

    def _parse_step(self, raw: str) -> ReasoningStep | None:
        """Распарсить JSON-ответ LLM в ReasoningStep."""
        parsed = _extract_json(raw)
        if parsed is None:
            # Не JSON — используем весь текст как thought
            return ReasoningStep(
                step_number=0,
                thought=raw[:2000],
                confidence=0.5,
            )

        thought = str(parsed.get("thought", parsed.get("final_response", "")))
        tool_calls = parsed.get("tool_calls")
        if tool_calls is not None and not isinstance(tool_calls, list):
            tool_calls = None

        try:
            confidence = float(parsed.get("confidence", 0.7))
        except (ValueError, TypeError):
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))

        return ReasoningStep(
            step_number=0,
            thought=thought,
            tool_calls=tool_calls,
            confidence=confidence,
        )

    def _format_trace(self, trace: ReasoningTrace) -> str:
        """Форматировать последние 5 шагов трейса для контекста LLM."""
        lines: list[str] = []
        for s in trace.steps[-5:]:
            marker = " [КОРРЕКЦИЯ]" if s.is_correction else ""
            lines.append(f"[Шаг {s.step_number}]{marker} {s.thought}")
            if s.observations:
                obs_str = json.dumps(s.observations, ensure_ascii=False, default=str)
                lines.append(f"  Результат: {obs_str[:200]}")
        return "\n".join(lines) if lines else "(нет предыдущих шагов)"

    def _has_errors(self, observations: list[dict]) -> bool:
        """Проверить, есть ли ошибки в результатах инструментов."""
        return any(
            isinstance(o, dict) and (o.get("error") or o.get("_fallback"))
            for o in observations
        )

    def _is_solved(self, trace: ReasoningTrace) -> bool:
        """Проверить, решена ли задача по трейсу."""
        if not trace.steps:
            return False
        last_step = trace.steps[-1]
        return last_step.confidence > 0.8

    def _plan_step_to_reasoning(
        self, step_def: dict, step_number: int
    ) -> ReasoningStep:
        """Преобразовать шаг плана в ReasoningStep."""
        thought = step_def.get("thought", step_def.get("description", ""))
        tool_calls = step_def.get("tool_calls", step_def.get("actions", []))
        if tool_calls and not isinstance(tool_calls, list):
            tool_calls = [tool_calls]
        confidence = float(step_def.get("confidence", 0.7))

        return ReasoningStep(
            step_number=step_number,
            thought=str(thought),
            tool_calls=tool_calls if tool_calls else None,
            confidence=confidence,
        )
