"""Специализированный роутер задач — эвристическая классификация без LLM.

Выбирает оптимальную модель по типу задачи и приоритетам cost/latency.
В отличие от auto_select_model (provider_manager), работает без доступа
к слотам провайдеров — чистая эвристика по ключевым словам.

Использование:
    task_type = SpecializedRouter.classify_task(user_text)
    model = await router.route(task_type)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SpecializedRouter:
    """Маршрутизирует задачи на оптимальные модели по типу и cost/latency."""

    # Приоритетные модели для каждого типа задач.
    # Первая в списке — предпочтительная.
    ROUTES: dict[str, list[str]] = {
        "planning": ["gpt-4o", "claude-3.5-sonnet", "deepseek-chat"],
        "coding": ["gpt-4o", "deepseek-coder", "claude-3.5-sonnet"],
        "classification": ["gpt-4o-mini", "gemini-1.5-flash"],
        "extraction": ["gpt-4o-mini", "gemini-1.5-flash"],
        "summarization": ["gpt-4o-mini", "gemini-1.5-flash"],
        "reasoning": ["gpt-4o", "claude-3.5-sonnet"],
        "general": ["gpt-4o-mini", "gemini-1.5-flash"],
    }

    async def route(self, task_type: str, context: dict | None = None) -> str:
        """Выбрать лучшую модель для типа задачи.

        Args:
            task_type: Тип задачи (planning, coding, classification, …).
            context: Опциональный контекст (не используется в эвристике, но
                     оставлен для будущих улучшений — например, история ошибок).

        Returns:
            Имя модели (например, "gpt-4o-mini").
        """
        candidates = self.ROUTES.get(task_type, self.ROUTES["general"])
        selected = candidates[0] if candidates else "gpt-4o-mini"
        logger.debug(
            "SpecializedRouter: task_type=%s → %s (candidates=%s)",
            task_type,
            selected,
            candidates,
        )
        return selected

    @staticmethod
    def classify_task(text: str) -> str:
        """Быстрая эвристическая классификация без вызова LLM.

        Правила (в порядке приоритета):
        - Ключевые слова «code», «function», «program», «api» → coding
        - Ключевые слова «plan», «organize», «schedule» → planning
        - Короткий текст (< 30 символов) → classification
        - Всё остальное → general

        Args:
            text: Пользовательский ввод.

        Returns:
            Строковой тип задачи.
        """
        text_l = text.lower()
        if any(w in text_l for w in ("code", "function", "program", "api")):
            return "coding"
        if any(w in text_l for w in ("plan", "organize", "schedule")):
            return "planning"
        if len(text_l) < 30:
            return "classification"
        return "general"
