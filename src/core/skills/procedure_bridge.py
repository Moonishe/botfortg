"""Мост между декларативными навыками и исполняемыми процедурами (Phase 3b).

Преобразует навыки из реестра в пошаговые Procedure-объекты,
которые может исполнять Agent Runtime. LLM анализирует skill-промпт
и извлекает из него исполняемые шаги с зависимостями и вызовами инструментов.
"""

from __future__ import annotations

import json as _json
import logging
import re
from dataclasses import dataclass, field

from src.llm.base import ChatMessage, TaskType

logger = logging.getLogger(__name__)


@dataclass
class Procedure:
    """Исполняемая процедура, полученная из декларативного навыка.

    Attributes:
        name: Имя навыка-родителя (напр. «translate_to_english»).
        goal: Человекочитаемое описание цели.
        steps: Список шагов [{description, tool_calls, depends_on}, ...].
        required_tools: Инструменты, необходимые для выполнения (имена).
        confidence: Уверенность в корректности процедуры (0.0–1.0).
        success_count: Число успешных исполнений.
        failure_count: Число неудачных исполнений.
    """

    name: str
    goal: str
    steps: list[dict] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    confidence: float = 0.7
    success_count: int = 0
    failure_count: int = 0


def _extract_json_from_response(text: str) -> str:
    """Извлекает JSON-блок из ответа LLM."""
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


class SkillProcedureBridge:
    """Конвертирует навыки в исполняемые процедуры и обратно.

    Использует LLM для семантического анализа skill-промпта и извлечения
    пошаговой структуры с зависимостями (топологический порядок).
    """

    async def skill_to_procedure(
        self,
        skill_name: str,
        owner_id: int,
    ) -> Procedure | None:
        """Конвертирует зарегистрированный навык в исполняемую процедуру.

        Шаги:
        1. Загружает навык из БД по имени.
        2. LLM анализирует body/description навыка и извлекает шаги.
        3. Возвращает Procedure с топологически упорядоченными шагами.

        Args:
            skill_name: Имя навыка (как в таблице skills.name).
            owner_id: Telegram user_id владельца навыка.

        Returns:
            Procedure или None если навык не найден / анализ не дал шагов.
        """
        from src.db.repo import get_or_create_user, list_skills
        from src.db.session import get_session
        from src.llm.router import build_provider

        # 1. Загружаем навык из БД
        async with get_session() as session:
            owner = await get_or_create_user(session, owner_id)
            skills = await list_skills(session, owner, limit=500)

        skill = None
        for s in skills:
            if s.name == skill_name or s.name.lower() == skill_name.lower():
                skill = s
                break

        if not skill:
            logger.debug(
                "skill_to_procedure: skill %r not found for owner %d",
                skill_name,
                owner_id,
            )
            return None

        # 2. Извлекаем шаги через LLM
        try:
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_id)
                provider = await build_provider(
                    session, owner, purpose="background", task_type=TaskType.SKILLS
                )
            steps = await self._extract_steps(skill.body or "", provider)
        except Exception:
            logger.exception(
                "skill_to_procedure: LLM extraction failed for skill %r", skill_name
            )
            return None

        # 3. Собираем инструменты из шагов
        tools_set: set[str] = set()
        if hasattr(skill, "trigger_patterns_json") and skill.trigger_patterns_json:
            for pat in skill.trigger_patterns_json:
                if isinstance(pat, str) and pat.startswith("tool:"):
                    tools_set.add(pat[len("tool:") :])
        if steps:
            for step in steps:
                for tc in step.get("tool_calls", []):
                    tools_set.add(tc)

        return Procedure(
            name=skill_name,
            goal=skill.description or skill_name,
            steps=steps,
            required_tools=sorted(tools_set),
            confidence=0.7,
            success_count=skill.success_count or 0,
            failure_count=skill.failure_count or 0,
        )

    async def procedure_to_skill_prompt(self, proc: Procedure) -> str:
        """Конвертирует процедуру обратно в формат skill-промпта.

        Генерирует человекочитаемый текст, пригодный для вставки в
        системный промпт агента.

        Args:
            proc: Исполняемая процедура.

        Returns:
            Строка промпта в формате, совместимом с skill-индексом.
        """
        lines = [
            f"Skill: {proc.name}",
            f"Goal: {proc.goal}",
            f"Confidence: {proc.confidence:.2f}",
            f"Success/Failure: {proc.success_count}/{proc.failure_count}",
        ]
        if proc.required_tools:
            lines.append(f"Tools: {', '.join(proc.required_tools)}")
        if proc.steps:
            lines.append("Steps:")
            for i, step in enumerate(proc.steps, 1):
                desc = step.get("description", f"Step {i}")
                deps = step.get("depends_on", [])
                tools = step.get("tool_calls", [])
                prefix = f"  {i}. {desc}"
                if deps:
                    prefix += f"  [depends on: {', '.join(map(str, deps))}]"
                if tools:
                    prefix += f"  [tools: {', '.join(tools)}]"
                lines.append(prefix)
        return "\n".join(lines)

    async def _extract_steps(
        self,
        skill_prompt: str,
        provider,
    ) -> list[dict]:
        """LLM извлекает исполняемые шаги из описания навыка.

        Args:
            skill_prompt: Текст навыка (body поле Skill-модели).
            provider: LLM-провайдер для анализа.

        Returns:
            Список шагов [{description, tool_calls, depends_on}, ...].
        """
        if not skill_prompt or not skill_prompt.strip():
            return []

        system = (
            "Ты — анализатор навыков. Твоя задача: из описания навыка извлечь "
            "пошаговую процедуру в виде JSON-массива. Каждый шаг содержит:\n"
            '- "description": краткое описание шага (строка);\n'
            '- "tool_calls": список имён инструментов для этого шага (массив строк);\n'
            '- "depends_on": список индексов шагов (0-based), от которых зависит этот шаг (массив int).\n\n'
            "Правила:\n"
            "- Шаги ДОЛЖНЫ быть топологически упорядочены (зависимость раньше зависимого).\n"
            "- Если инструмент не указан явно — оставь tool_calls пустым.\n"
            "- Верни ТОЛЬКО JSON-массив, без markdown-обёрток.\n"
            "- Если шагов нет (skill — просто совет) — верни пустой массив []."
        )

        try:
            response = await provider.chat(
                [
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=f"Навык:\n{skill_prompt[:2000]}"),
                ],
                task_type=TaskType.SKILLS,
            )
        except Exception:
            logger.exception("_extract_steps: LLM call failed")
            return []

        json_str = _extract_json_from_response(response)
        try:
            steps = _json.loads(json_str)
        except _json.JSONDecodeError:
            logger.debug("_extract_steps: invalid JSON from LLM: %r", json_str[:200])
            return []

        if not isinstance(steps, list):
            return []

        # Валидация структуры каждого шага
        valid_steps: list[dict] = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            desc = step.get("description", f"Step {i + 1}")
            tool_calls = step.get("tool_calls", [])
            depends_on = step.get("depends_on", [])
            if not isinstance(tool_calls, list):
                tool_calls = []
            if not isinstance(depends_on, list):
                depends_on = []
            valid_steps.append(
                {
                    "description": str(desc),
                    "tool_calls": [str(t) for t in tool_calls],
                    "depends_on": [
                        int(d) for d in depends_on if isinstance(d, (int, float))
                    ],
                }
            )

        logger.info(
            "_extract_steps: extracted %d steps from skill prompt", len(valid_steps)
        )
        return valid_steps
