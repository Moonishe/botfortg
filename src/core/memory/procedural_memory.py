"""Процедурная память — хранение и извлечение усвоенных процедур (Phase 3b).

Хранит исполняемые процедуры (Procedure), полученные из навыков через
Skill→Procedure bridge. Поддерживает индукцию новых процедур из эпизодов
и адаптацию на основе обратной связи (успех/неудача/коррекция пользователя).
"""

from __future__ import annotations

import json as _json
import logging
import re
from typing import Any

from src.llm.base import ChatMessage, TaskType

logger = logging.getLogger(__name__)


def _extract_json_from_response(text: str) -> str:
    m = re.search(r"```(?:json|JSON)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    brace_m = re.search(r"\{[\s\S]*\}", text)
    if brace_m:
        return brace_m.group(0)
    array_m = re.search(r"\[[\s\S]*\]", text)
    if array_m:
        return array_m.group(0)
    return text.strip()


class ProceduralMemory:
    """Хранилище усвоенных процедур с поддержкой обучения и адаптации.

    # NOTE: In-memory storage. Data lost on restart. DB persistence planned for Phase 7.
    Поддерживает:
    - Поиск существующей процедуры по Jaccard-сходству цели.
    - Индукцию новой процедуры из эпизодов через LLM.
    - Обновление confidence на основе результатов исполнения.
    - Адаптацию при пользовательской коррекции.
    """

    def __init__(self):
        self._procedures: dict[str, Any] = {}  # name → Procedure

    async def find_or_induce(
        self,
        goal: str,
        context: dict,
    ) -> Any | None:
        """Находит существующую процедуру или индуцирует новую из эпизодов.

        Стратегия:
        1. Ищем существующую процедуру с похожей целью (Jaccard > 0.3).
        2. Если не нашли и есть эпизоды — вызываем LLM для индукции.
        3. Результат кэшируем в self._procedures.

        Args:
            goal: Человекочитаемое описание цели («переведи текст на английский»).
            context: Контекст с ключом "episodes" — список эпизодов для индукции.

        Returns:
            Procedure или None если ничего не найдено/индуцировано.
        """
        # 1. Поиск существующей
        for name, proc in self._procedures.items():
            proc_goal = getattr(proc, "goal", "")
            if proc_goal and self._goal_matches(goal, proc_goal):
                logger.debug("find_or_induce: found cached procedure %r", name)
                return proc

        # 2. Индукция из эпизодов
        episodes = context.get("episodes") or context.get("episode_history", [])
        if episodes:
            try:
                induced = await self._induce_from_episodes(goal, episodes)
                if induced is not None:
                    self._procedures[getattr(induced, "name", goal)] = induced
                    logger.info(
                        "find_or_induce: induced new procedure %r from %d episodes",
                        getattr(induced, "name", goal),
                        len(episodes),
                    )
                    return induced
            except Exception:
                logger.exception("find_or_induce: induction failed for goal %r", goal)

        return None

    def _goal_matches(self, goal: str, proc_goal: str) -> bool:
        """Простое Jaccard-сходство по словам (без эмбеддингов).

        Порог: доля общих слов > 0.3. Подходит для быстрого фильтра,
        не требует LLM/эмбеддингов.

        Args:
            goal: Целевой запрос.
            proc_goal: Цель сохранённой процедуры.

        Returns:
            True если цели достаточно похожи.
        """
        g_words = set(goal.lower().split())
        p_words = set(proc_goal.lower().split())
        if not g_words or not p_words:
            return False
        intersection = g_words & p_words
        union = g_words | p_words
        if not union:
            return False
        return len(intersection) / len(union) > 0.3

    async def learn(self, proc, execution_result: dict):
        """Обновляет confidence процедуры на основе результата исполнения.

        Успех → confidence растёт (но не выше 0.95).
        Неудача → счётчик неудач растёт (confidence не меняется при единичной
        неудаче — адаптация происходит только при коррекции пользователя).

        Args:
            proc: Объект Procedure.
            execution_result: dict с ключом "success" (bool).
        """
        if not proc:
            return
        if execution_result.get("success"):
            proc.success_count = getattr(proc, "success_count", 0) + 1
            proc.confidence = min(0.95, getattr(proc, "confidence", 0.7) + 0.02)
            logger.debug(
                "learn: procedure %r success, confidence=%.2f",
                proc.name,
                proc.confidence,
            )
        else:
            proc.failure_count = getattr(proc, "failure_count", 0) + 1
            logger.debug(
                "learn: procedure %r failure, count=%d", proc.name, proc.failure_count
            )

    async def adapt_from_correction(self, proc: Any, correction: str):
        """Пользователь предоставил коррекцию — снижаем confidence процедуры.

        Confidence снижается на 0.1 (но не ниже 0.3), чтобы избежать
        повторного использования неверной процедуры без полного сброса.

        Args:
            proc: Объект Procedure.
            correction: Текст коррекции от пользователя (для будущего анализа).
        """
        if not proc:
            return
        proc.confidence = max(0.3, getattr(proc, "confidence", 0.7) - 0.1)
        logger.info(
            "adapt_from_correction: procedure %r confidence dropped to %.2f (correction: %r)",
            proc.name,
            proc.confidence,
            correction[:100],
        )

    async def _induce_from_episodes(
        self,
        goal: str,
        episodes: list[dict],
    ) -> Any | None:
        """LLM индуцирует процедуру из прошлых эпизодов.

        Отправляет LLM описание цели и список успешных эпизодов,
        просит сформировать пошаговую процедуру.

        Args:
            goal: Описание цели.
            episodes: Список эпизодов [{description, steps, outcome}, ...].

        Returns:
            Procedure или None если индукция не дала результата.
        """
        from src.core.skills.procedure_bridge import Procedure
        from src.db.session import get_session
        from src.llm.router import build_provider

        if not episodes:
            return None

        # Форматируем эпизоды для LLM
        episode_lines: list[str] = []
        for i, ep in enumerate(episodes[:5], 1):  # не более 5 для экономии токенов
            desc = ep.get("description", ep.get("summary", f"Эпизод {i}"))
            outcome = ep.get("outcome", ep.get("result", "неизвестно"))
            episode_lines.append(f"Эпизод {i}: {desc}\n  Исход: {outcome}")

        system = (
            "Ты — анализатор поведения. На основе прошлых эпизодов индуцируй "
            "пошаговую процедуру для достижения цели. Верни JSON:\n"
            '{"name": "procedure_name", "goal": "...", "steps": ['
            '{"description": "...", "tool_calls": [], "depends_on": []}]}\n\n'
            "Правила:\n"
            "- name: snake_case идентификатор.\n"
            "- steps: упорядочены по зависимостям.\n"
            '- Если не можешь индуцировать — верни {"error": "причина"}.\n'
            "- Верни ТОЛЬКО JSON, без markdown."
        )

        user_prompt = f"Цель: {goal}\n\nПрошлые эпизоды:\n" + "\n".join(episode_lines)

        try:
            async with get_session() as session:
                # Пытаемся найти пользователя; если нет — fallback
                owner = None
                try:
                    from src.db.repo import get_or_create_user as _get_or_create

                    owner = await _get_or_create(session, 0)  # fallback: guest user
                except Exception:
                    logger.debug("Non-critical error", exc_info=True)

                if owner is None:
                    logger.debug(
                        "_induce_from_episodes: no owner, cannot build provider"
                    )
                    return None

                provider = await build_provider(
                    session, owner, purpose="background", task_type=TaskType.SKILLS
                )

            response = await provider.chat(
                [
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user_prompt),
                ],
                task_type=TaskType.SKILLS,
            )
        except Exception:
            logger.exception("_induce_from_episodes: LLM call failed")
            return None

        if response is None:
            return None

        json_str = _extract_json_from_response(response)
        try:
            data = _json.loads(json_str)
        except _json.JSONDecodeError:
            logger.debug(
                "_induce_from_episodes: invalid JSON from LLM: %r", json_str[:200]
            )
            return None

        if not isinstance(data, dict) or "error" in data:
            logger.debug(
                "_induce_from_episodes: LLM returned error: %s",
                data.get("error", "unknown"),
            )
            return None

        name = str(data.get("name", goal)).strip().replace(" ", "_")[:96]
        proc_goal = str(data.get("goal", goal)).strip()
        steps = data.get("steps", [])
        if not isinstance(steps, list):
            steps = []

        valid_steps: list[dict] = []
        for s in steps:
            if not isinstance(s, dict):
                continue
            valid_steps.append(
                {
                    "description": str(s.get("description", "")),
                    "tool_calls": [str(t) for t in s.get("tool_calls", [])],
                    "depends_on": [
                        int(d)
                        for d in s.get("depends_on", [])
                        if isinstance(d, (int, float))
                    ],
                }
            )

        return Procedure(
            name=name,
            goal=proc_goal,
            steps=valid_steps,
            required_tools=[],
            confidence=0.5,  # Индуцированные процедуры стартуют с низкой уверенностью
            success_count=0,
            failure_count=0,
        )

    def stats(self) -> dict:
        """Статистика процедурной памяти для отладки."""
        total = len(self._procedures)
        total_success = sum(
            getattr(p, "success_count", 0) for p in self._procedures.values()
        )
        total_failure = sum(
            getattr(p, "failure_count", 0) for p in self._procedures.values()
        )
        avg_conf = sum(
            getattr(p, "confidence", 0) for p in self._procedures.values()
        ) / max(total, 1)
        return {
            "total_procedures": total,
            "total_successes": total_success,
            "total_failures": total_failure,
            "avg_confidence": round(avg_conf, 3),
        }
