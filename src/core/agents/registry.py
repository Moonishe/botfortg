"""Agent Registry — каталог специализированных агентов (Phase 3b).

Хранит метаданные агентов: имя, описание, инструменты, предпочтительную модель.
Предоставляет классификацию задач и делегирование на основе ключевых слов
или MetaEvaluation (если доступен MetaReasoner).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AgentSpec:
    """Метаданные специализированного агента.

    Attributes:
        name: Уникальное имя агента («researcher», «coder», «planner», «analyst»).
        description: Человекочитаемое описание задач агента.
        tools: Список имён инструментов, доступных агенту.
        model_hint: Предпочтительная модель для исполнения (напр. «gpt-4o»).
    """

    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    model_hint: str = "gpt-4o"


class AgentRegistry:
    """Реестр специализированных агентов с классификацией задач.

    Используется для:
    - Поиска агента по имени (``get``).
    - Перечисления всех агентов (``list_all``).
    - Классификации задачи и делегирования (``classify_and_delegate``).

    Классификация работает в двух режимах:
    1. Если передан ``meta_eval`` (MetaEvaluation) с флагом should_delegate —
       использует рекомендацию MetaReasoner.
    2. Иначе — keyword-based классификация (быстрая, без LLM).
    """

    AGENTS: dict[str, AgentSpec] = {
        "researcher": AgentSpec(
            "researcher",
            "Глубокий поиск: веб-поиск, анализ документации, фактчекинг",
            ["web_search", "memory_search", "write_memory"],
            "gpt-4o",
        ),
        "coder": AgentSpec(
            "coder",
            "Задачи на код: чтение/запись файлов, тестирование, отладка",
            ["read_file", "write_file", "run_command", "memory_search"],
            "gpt-4o",
        ),
        "planner": AgentSpec(
            "planner",
            "Сложное планирование: HTN-декомпозиция, распределение подзадач",
            ["plan_task", "memory_search", "write_memory"],
            "gpt-4o",
        ),
        "analyst": AgentSpec(
            "analyst",
            "Анализ данных: SQL, графики, статистика, отчёты",
            ["memory_search", "write_memory"],
            "gpt-4o",
        ),
    }

    @classmethod
    def get(cls, name: str) -> AgentSpec | None:
        """Возвращает спецификацию агента по имени.

        Args:
            name: Имя агента (case-insensitive).

        Returns:
            AgentSpec или None если агент не найден.
        """
        return cls.AGENTS.get(name.lower())

    @classmethod
    def list_all(cls) -> list[AgentSpec]:
        """Возвращает список всех зарегистрированных агентов."""
        return list(cls.AGENTS.values())

    @classmethod
    async def classify_and_delegate(
        cls,
        task: str,
        context: dict | None = None,
        meta_eval=None,
    ) -> tuple[str, AgentSpec | None]:
        """Классифицирует задачу и выбирает лучшего агента.

        Args:
            task: Текст задачи.
            context: Дополнительный контекст (опционально).
            meta_eval: MetaEvaluation от MetaReasoner (опционально).
                Если передан и hasattr(meta_eval, 'should_delegate') == True
                и delegate_to указан — используется рекомендация MetaReasoner.

        Returns:
            Кортеж (имя_агента, AgentSpec).
            По умолчанию — ("researcher", researcher_spec).
        """
        # Режим 1: MetaReasoner рекомендует делегирование
        if meta_eval is not None:
            try:
                should_del = getattr(meta_eval, "should_delegate", False)
                delegate_to = getattr(meta_eval, "delegate_to", None)
                if should_del and delegate_to:
                    agent = cls.get(delegate_to)
                    if agent is not None:
                        logger.info(
                            "classify_and_delegate: meta_eval delegate → %s",
                            delegate_to,
                        )
                        return delegate_to, agent
            except Exception:
                logger.debug(
                    "classify_and_delegate: meta_eval access failed, falling back to keywords",
                    exc_info=True,
                )

        # Режим 2: Keyword-based классификация
        task_lower = task.lower()

        # Coder: явные отсылки к программированию
        if any(
            w in task_lower
            for w in (
                "code",
                "программ",
                "function",
                "api",
                "debug",
                "функци",
                "класс",
                "def ",
                "import ",
                "баг",
                "ошибк",
                "исправь код",
                "напиши код",
                "скрипт",
                "тест",
            )
        ):
            return "coder", cls.get("coder")

        # Researcher: поиск, исследование
        if any(
            w in task_lower
            for w in (
                "search",
                "research",
                "find",
                "найди",
                "поиск",
                "узнай",
                "проверь факт",
                "что такое",
                "объясни",
                "расскажи",
                "документаци",
            )
        ):
            return "researcher", cls.get("researcher")

        # Planner: планирование, организация
        if any(
            w in task_lower
            for w in (
                "plan",
                "organize",
                "schedule",
                "план",
                "организуй",
                "распиши",
                "составь план",
                "шаги",
                "этапы",
                "декомпозиц",
                "разбей задачу",
            )
        ):
            return "planner", cls.get("planner")

        # Analyst: анализ данных, статистика
        if any(
            w in task_lower
            for w in (
                "analyze",
                "data",
                "stats",
                "анализ",
                "данные",
                "статистик",
                "график",
                "отчёт",
                "report",
                "сравни",
                "посчитай",
                "sql",
            )
        ):
            return "analyst", cls.get("analyst")

        # Default: researcher (наиболее универсальный)
        return "researcher", cls.get("researcher")
