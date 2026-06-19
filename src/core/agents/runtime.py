"""Agent Runtime — пошаговое исполнение планов с чекпоинтингом и бюджетом.

Предоставляет:
- ``AgentRuntime`` — основной движок: принимает план, исполняет шаги,
  сохраняет чекпоинты, контролирует бюджет токенов.
- ``AgentCheckpoint`` — снапшот состояния для возобновления.
- ``TokenBudget`` — учёт расхода токенов с лимитами.
- ``AgentState`` — рантайм-состояние агента: шаги, история, ошибки, output.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# Data Classes
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class AgentCheckpoint:
    """Снапшот состояния агента в точке сохранения.

    Attributes:
        step_index: Номер шага, на котором сделан чекпоинт (0-based).
        working_memory_snapshot: Копия working memory на момент чекпоинта.
        agent_state: Состояние агента (step, errors, history).
        memory_versions: Версии памяти {memory_id: version_at_checkpoint}.
        timestamp: Время создания чекпоинта (UTC).
    """

    step_index: int
    working_memory_snapshot: dict[str, Any]
    agent_state: dict[str, Any]
    memory_versions: dict[str, Any]
    timestamp: datetime

    @property
    def checkpoint_id(self) -> str:
        """Уникальный идентификатор чекпоинта (timestamp + step + object id)."""
        return (
            f"cp_{self.step_index}_{int(self.timestamp.timestamp())}-{id(self) % 1000}"
        )


@dataclass
class TokenBudget:
    """Контроль расхода токенов на исполнение агента.

    Attributes:
        max_total: Максимальное количество токенов на весь запуск.
        max_per_step: Максимальное количество токенов на один шаг.
        used: Использовано токенов.
        exceeded: Флаг превышения лимита.
    """

    max_total: int
    max_per_step: int
    used: int = 0
    exceeded: bool = False

    def consume(self, tokens: int) -> bool:
        """Зарегистрировать расход токенов.

        Returns:
            True если лимит превышен после расхода, False иначе.
        """
        self.used += tokens
        if self.used > self.max_total:
            self.exceeded = True
        return self.exceeded

    def step_budget_exceeded(self, tokens: int) -> bool:
        """Проверить, превысит ли шаг лимит на шаг."""
        return tokens > self.max_per_step


# ══════════════════════════════════════════════════════════════════════════
# AgentState
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class AgentState:
    """Рантайм-состояние исполнения агента.

    Attributes:
        current_step: Текущий индекс шага (0-based).
        history: История результатов шагов.
        checkpoints: Сохранённые чекпоинты.
        errors: Накопленные ошибки.
        output: Финальный/промежуточный результат.
        run_id: Уникальный ID запуска.
        started_at: Время старта.
    """

    current_step: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[AgentCheckpoint] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def snapshot(self) -> dict[str, Any]:
        """Снять снапшот состояния для чекпоинта."""
        return {
            "step": self.current_step,
            "errors": list(self.errors),
            "history_length": len(self.history),
            "output": dict(self.output),
        }


# ══════════════════════════════════════════════════════════════════════════
# AgentRuntime
# ══════════════════════════════════════════════════════════════════════════


class AgentRuntime:
    """Исполняет планы агентов пошагово с чекпоинтингом.

    Контролирует:
    - Бюджет токенов (общий + на шаг).
    - Максимальное количество шагов.
    - Сохранение чекпоинтов каждые N шагов и на ошибках.
    - Гейты подтверждения пользователем.
    - Возобновление с чекпоинта.

    Атрибуты класса:
        MAX_STEPS: Жёсткий лимит шагов (защита от бесконечного цикла).
        BUDGET_DEFAULT: Бюджет токенов по умолчанию, если не задан в конфиге.
        CHECKPOINT_INTERVAL: Чекпоинт каждые N шагов.
    """

    MAX_STEPS: int = 20
    BUDGET_DEFAULT: int = 50000
    CHECKPOINT_INTERVAL: int = 5
    PARALLEL_TOOLS: int = 3  # макс. параллельных вызовов инструментов

    @property
    def max_steps(self) -> int:
        """Жёсткий лимит шагов — из конфига или class default."""
        try:
            from src.config import settings

            return max(1, settings.agent_max_steps)
        except Exception:
            return self.MAX_STEPS

    # ── Public API ──────────────────────────────────────────────────────

    async def run(
        self,
        plan: dict[str, Any],
        context: dict[str, Any] | None = None,
        session: Any = None,
        user: Any = None,
    ) -> dict[str, Any]:
        """Исполнить план пошагово с чекпоинтингом и контролем бюджета.

        Args:
            plan: План (dict со steps, goal, metadata).
            context: Контекст исполнения (working_memory, user_info, ...).
            session: Сессия БД (передаётся runtime).
            user: Объект пользователя.

        Returns:
            dict с полями: status, steps_completed, output, checkpoint, ...
        """
        context = dict(context or {})
        state = AgentState()
        budget = TokenBudget(
            max_total=self._max_total(),
            max_per_step=self._max_per_step(),
        )

        steps: list[dict[str, Any]] = plan.get("steps", [])
        if not steps:
            logger.warning(
                "AgentRuntime.run: план без шагов — plan_id=%s", plan.get("id", "?")
            )
            return {
                "status": "completed",
                "steps_completed": 0,
                "output": {},
                "message": "План пуст",
            }

        logger.info(
            "AgentRuntime.run: старт plan_id=%s, шагов=%d, run_id=%s",
            plan.get("id", "?"),
            len(steps),
            state.run_id,
        )

        try:
            for i, step_def in enumerate(steps):
                state.current_step = i

                # ── Бюджет: глобальный лимит ──
                if budget.exceeded:
                    logger.warning(
                        "AgentRuntime.run: бюджет превышен на шаге %d/%d (used=%d/%d)",
                        i,
                        len(steps),
                        budget.used,
                        budget.max_total,
                    )
                    return {
                        "status": "budget_exceeded",
                        "steps_completed": i,
                        "output": state.output,
                        "run_id": state.run_id,
                    }

                # ── Жёсткий лимит шагов ──
                if i >= self.max_steps:
                    logger.warning(
                        "AgentRuntime.run: превышен лимит шагов max_steps=%d",
                        self.max_steps,
                    )
                    return {
                        "status": "max_steps_exceeded",
                        "steps_completed": i,
                        "output": state.output,
                        "run_id": state.run_id,
                    }

                # ── Исполнить шаг ──
                result = await self._execute_step(
                    step_def, context, state, budget, session, user
                )
                state.history.append(result)

                if result.get("error"):
                    state.errors.append(result["error"])
                    logger.info(
                        "AgentRuntime.run: ошибка на шаге %d — сохраняю чекпоинт",
                        i,
                    )
                    await self._save_checkpoint(state, context)

                # ── Периодический чекпоинт ──
                if (i + 1) % self.CHECKPOINT_INTERVAL == 0:
                    await self._save_checkpoint(state, context)

                # ── Гейт подтверждения пользователем ──
                if step_def.get("is_checkpoint") and not result.get("_confirmed"):
                    await self._save_checkpoint(state, context)
                    logger.info(
                        "AgentRuntime.run: ожидание подтверждения на шаге %d",
                        i,
                    )
                    return {
                        "status": "awaiting_confirmation",
                        "checkpoint": self._checkpoint_to_dict(state.checkpoints[-1])
                        if state.checkpoints
                        else None,
                        "steps_completed": i + 1,
                        "output": state.output,
                        "run_id": state.run_id,
                    }

            # ── Успешное завершение ──
            logger.info(
                "AgentRuntime.run: успешно завершён plan_id=%s, шагов=%d, run_id=%s",
                plan.get("id", "?"),
                len(steps),
                state.run_id,
            )
            return {
                "status": "completed",
                "steps_completed": len(steps),
                "output": state.output,
                "checkpoints": [
                    self._checkpoint_to_dict(cp) for cp in state.checkpoints
                ],
                "run_id": state.run_id,
                "budget_used": budget.used,
            }

        except Exception as exc:
            logger.exception(
                "AgentRuntime.run: фатальная ошибка run_id=%s", state.run_id
            )
            await self._save_checkpoint(state, context)
            return {
                "status": "error",
                "error": str(exc),
                "steps_completed": state.current_step,
                "output": state.output,
                "checkpoint": self._checkpoint_to_dict(state.checkpoints[-1])
                if state.checkpoints
                else None,
                "run_id": state.run_id,
            }

    async def resume(
        self,
        checkpoint_id: str,
        context: dict[str, Any] | None = None,
        session: Any = None,
        user: Any = None,
    ) -> dict[str, Any]:
        """Возобновить исполнение с чекпоинта.

        Ищет сохранённый чекпоинт по ``checkpoint_id``, восстанавливает
        состояние и продолжает исполнение оставшихся шагов плана.

        Args:
            checkpoint_id: Идентификатор чекпоинта (checkpoint_id из AgentCheckpoint).
            context: Контекст исполнения (перезаписывает working_memory
                     снапшотом из чекпоинта).
            session: Сессия БД.
            user: Объект пользователя.

        Returns:
            dict со статусом возобновления: ok, checkpoint, resume_step.
        """
        # Чекпоинты хранятся в снапшоте context.working_memory._checkpoints
        # или передаются явно.  Ищем по checkpoint_id.
        context = dict(context or {})
        wm = context.get("working_memory") or {}
        checkpoints: list = (
            wm.get("_agent_checkpoints") or context.get("_agent_checkpoints") or []
        )

        target_cp: AgentCheckpoint | None = None
        for cp_raw in checkpoints:
            if isinstance(cp_raw, AgentCheckpoint):
                if cp_raw.checkpoint_id == checkpoint_id:
                    target_cp = cp_raw
                    break
            elif isinstance(cp_raw, dict):
                cid = cp_raw.get("checkpoint_id") or ""
                if cid == checkpoint_id:
                    target_cp = AgentCheckpoint(
                        step_index=cp_raw.get("step_index", 0),
                        working_memory_snapshot=cp_raw.get(
                            "working_memory_snapshot", {}
                        ),
                        agent_state=cp_raw.get("agent_state", {}),
                        memory_versions=cp_raw.get("memory_versions", {}),
                        timestamp=datetime.now(UTC),
                    )
                    break

        if target_cp is None:
            logger.warning("AgentRuntime.resume: чекпоинт %s не найден", checkpoint_id)
            return {"ok": False, "error": f"Checkpoint '{checkpoint_id}' not found"}

        # Восстанавливаем working memory из снапшота
        context["working_memory"] = dict(target_cp.working_memory_snapshot)

        resume_from = target_cp.step_index + 1  # resume from NEXT step after checkpoint
        logger.info(
            "AgentRuntime.resume: возобновление с шага %d, checkpoint_id=%s",
            resume_from,
            checkpoint_id,
        )
        return {
            "ok": True,
            "resume_step": resume_from,
            "checkpoint": self._checkpoint_to_dict(target_cp),
            "agent_state": dict(target_cp.agent_state),
            "message": (
                f"Готов к возобновлению с шага {target_cp.step_index}. "
                "Передайте resume_step в план для продолжения."
            ),
        }

    # ── Internal Helpers ────────────────────────────────────────────────

    async def _execute_step(
        self,
        step_def: dict[str, Any],
        context: dict[str, Any],
        state: AgentState,
        budget: TokenBudget,
        session: Any = None,
        user: Any = None,
    ) -> dict[str, Any]:
        """Исполнить один шаг плана.

        Args:
            step_def: Определение шага (description, tool_calls, is_checkpoint, ...).
            context: Контекст исполнения.
            state: Текущее состояние агента.
            budget: Бюджет токенов.
            session: Сессия БД.
            user: Объект пользователя.

        Returns:
            dict с results, status, thought.
        """
        tool_calls: list[dict[str, Any]] = step_def.get("tool_calls", [])
        description: str = step_def.get("description", "") or step_def.get("action", "")

        if not tool_calls:
            logger.debug("AgentRuntime._execute_step: шаг без tool_calls — пропущен")
            return {"thought": description, "status": "skipped", "results": []}

        # ── Бюджет шага ──
        step_tokens = len(tool_calls) * 500  # грубая оценка
        if budget.step_budget_exceeded(step_tokens):
            logger.warning(
                "AgentRuntime._execute_step: шаг превышает per-step бюджет (%d > %d)",
                step_tokens,
                budget.max_per_step,
            )
            return {
                "thought": description,
                "status": "step_budget_exceeded",
                "error": f"Step requires ~{step_tokens} tokens, max per step is {budget.max_per_step}",
            }

        # ── Исполнить инструменты с параллелизмом ──
        sem = asyncio.Semaphore(self.PARALLEL_TOOLS)

        async def _exec_one(tc: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                tool_name: str = tc.get("tool", "")
                params: dict[str, Any] = dict(tc.get("params", {}))
                if not tool_name:
                    return {"error": "tool name missing in tool_call"}

                # Пробрасываем runtime-объекты в params
                if session is not None:
                    params.setdefault("session", session)
                if user is not None:
                    params.setdefault("user", user)
                params.setdefault("context", context)

                try:
                    # Lazy import — избегаем циклических зависимостей
                    from src.core.actions.tool_registry import tool_registry

                    # Защита от bypass: _confirmed НЕ должен приходить из params
                    params.pop("_confirmed", None)
                    return await tool_registry.execute(
                        tool_name, _confirmed=False, **params
                    )
                except Exception as exc:
                    logger.exception(
                        "AgentRuntime._execute_step: tool %s рухнул",
                        tool_name,
                    )
                    return {"error": str(exc), "tool": tool_name}

        raw_results = await asyncio.gather(
            *[_exec_one(tc) for tc in tool_calls],
            return_exceptions=True,
        )

        # Не даём CancelledError затеряться — propagation обязателен
        # (return_exceptions=True ловит ВСЁ, включая CancelledError)
        for r in raw_results:
            if isinstance(r, asyncio.CancelledError):
                raise r

        # Нормализуем результаты
        normalized: list[dict[str, Any]] = []
        for i, r in enumerate(raw_results):
            if isinstance(r, BaseException):
                tool_name = (
                    tool_calls[i].get("tool", "?") if i < len(tool_calls) else "?"
                )
                normalized.append({"error": str(r), "tool": tool_name})
            elif isinstance(r, dict):
                normalized.append(r)
            else:
                normalized.append({"result": r})

        # ── Учесть расход токенов ──
        budget.consume(step_tokens)

        status = "ok"
        errors_in_step = [r for r in normalized if r.get("error")]
        if errors_in_step:
            if len(errors_in_step) == len(normalized):
                status = "all_failed"
            else:
                status = "partial_failure"

        return {
            "thought": description,
            "status": status,
            "results": normalized,
            "step": state.current_step,
        }

    async def _save_checkpoint(
        self,
        state: AgentState,
        context: dict[str, Any],
    ) -> None:
        """Сохранить чекпоинт исполнения.

        Создаёт ``AgentCheckpoint``, добавляет в state.checkpoints и
        сохраняет в context для персистентности (через working_memory).

        Args:
            state: Текущее состояние агента.
            context: Контекст исполнения (мутабельный — записываем чекпоинты).
        """
        checkpoint = AgentCheckpoint(
            step_index=state.current_step,
            working_memory_snapshot=dict(context.get("working_memory", {})),
            agent_state=state.snapshot(),
            memory_versions=dict(context.get("memory_versions", {})),
            timestamp=datetime.now(UTC),
        )
        state.checkpoints.append(checkpoint)

        # Сохраняем в context для внешнего доступа
        wm = context.setdefault("working_memory", {})
        wm.setdefault("_agent_checkpoints", []).append(checkpoint)
        context.setdefault("_agent_checkpoints", wm["_agent_checkpoints"])

        logger.debug(
            "AgentRuntime._save_checkpoint: шаг %d, run_id=%s, checkpoint_id=%s",
            state.current_step,
            state.run_id,
            checkpoint.checkpoint_id,
        )

    def _checkpoint_to_dict(self, cp: AgentCheckpoint) -> dict[str, Any]:
        """Сериализовать чекпоинт в словарь для JSON-ответа."""
        return {
            "checkpoint_id": cp.checkpoint_id,
            "step_index": cp.step_index,
            "working_memory_snapshot": cp.working_memory_snapshot,
            "agent_state": cp.agent_state,
            "memory_versions": cp.memory_versions,
            "timestamp": cp.timestamp.isoformat(),
        }

    def _max_total(self) -> int:
        """Получить макс. бюджет токенов из конфига или default."""
        try:
            from src.config import settings

            return settings.agent_token_budget
        except Exception:
            return self.BUDGET_DEFAULT

    def _max_per_step(self) -> int:
        """Получить макс. бюджет на шаг (20% от общего или 5000)."""
        try:
            from src.config import settings

            return max(1000, settings.agent_token_budget // 10)
        except Exception:
            return 5000


# ══════════════════════════════════════════════════════════════════════════
# Module-level singleton
# ══════════════════════════════════════════════════════════════════════════

agent_runtime = AgentRuntime()
"""Глобальный экземпляр AgentRuntime для повторного использования."""
