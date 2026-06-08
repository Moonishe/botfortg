"""NL Programming — преобразование естественного языка в BackgroundGoal.

Пример:
    «Каждый понедельник в 9:00 собери встречи за неделю»
    → BackgroundGoal(description="Собрать встречи за неделю",
                     frequency="weekly monday 9:00", …)

Использует LLM (purpose="reasoning") для парсинга намерения пользователя
и создания структурированной фоновой задачи.

Включается/выключается флагом nl_programming_enabled в config.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════
#  BackgroundGoal — фоновое намерение, извлечённое из NL
# ═════════════════════════════════════════════════════════════════════════


@dataclass
class BackgroundGoal:
    """Фоновая цель, созданная из естественно-языкового описания.

    Используется планировщиком для автоматического выполнения
    повторяющихся действий (сбор встреч, дайджесты, напоминания).
    """

    id: str  # Уникальный идентификатор: "nl_{user_id}_{timestamp}"
    user_id: int
    description: str  # Человеко-читаемое описание цели
    frequency: str  # Cron-подобная строка или "daily 9:00", "weekly monday 9:00"

    def __repr__(self) -> str:
        return (
            f"BackgroundGoal(id={self.id!r}, user_id={self.user_id}, "
            f"description={self.description!r}, frequency={self.frequency!r})"
        )


# ═════════════════════════════════════════════════════════════════════════
#  NLProgrammer — парсер NL → BackgroundGoal
# ═════════════════════════════════════════════════════════════════════════


class NLProgrammer:
    """Парсит естественно-языковые описания в структурированные BackgroundGoal."""

    # Системный промпт для LLM — объясняет формат и ожидания
    _SYSTEM_PROMPT = (
        "Ты — парсер естественного языка в структурированные задачи. "
        "Преобразуй описание пользователя в JSON с полями:\n"
        '  "description": краткое описание действия,\n'
        '  "frequency": расписание (например "daily 9:00", '
        '"weekly monday 9:00", "hourly").\n'
        "Верни ТОЛЬКО JSON, без пояснений и маркдауна."
    )

    async def parse(
        self,
        user_input: str,
        session: AsyncSession,
        user: User,
    ) -> BackgroundGoal | None:
        """Преобразовать NL-описание в BackgroundGoal через LLM.

        Args:
            user_input: Пользовательский текст (например,
                        «Каждый понедельник в 9: собери встречи за неделю»).
            session: SQLAlchemy AsyncSession.
            user: Объект пользователя.

        Returns:
            BackgroundGoal при успешном парсинге, None при ошибке.
        """
        from src.llm.base import ChatMessage
        from src.llm.router import build_provider

        try:
            provider = await build_provider(
                session, user, purpose="reasoning", task_type="background"
            )
        except Exception:
            logger.exception(
                "NLProgrammer: не удалось получить провайдер для user=%s",
                user.telegram_id,
            )
            return None

        if provider is None:
            logger.warning(
                "NLProgrammer: провайдер недоступен для user=%s",
                user.telegram_id,
            )
            return None

        messages = [
            ChatMessage(role="system", content=self._SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=(
                    f"Преобразуй описание в структуру задачи.\n"
                    f"Описание: {user_input}\n"
                    "Верни JSON: "
                    '{"description": "...", "frequency": "..."}'
                ),
            ),
        ]

        try:
            result = await provider.chat(messages, task_type="background")
        except Exception:
            logger.exception(
                "NLProgrammer: ошибка вызова LLM для user=%s",
                user.telegram_id,
            )
            return None

        try:
            parsed = json.loads(self._extract_json(result))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "NLProgrammer: не удалось распарсить JSON из ответа LLM: %s (user=%s)",
                exc,
                user.telegram_id,
            )
            return None

        if not isinstance(parsed, dict) or "description" not in parsed:
            logger.warning(
                "NLProgrammer: ответ LLM не содержит 'description' (user=%s): %s",
                user.telegram_id,
                parsed,
            )
            return None

        goal = BackgroundGoal(
            id=f"nl_{user.telegram_id}_{int(time.time())}",
            user_id=user.telegram_id,
            description=str(parsed.get("description", "")),
            frequency=str(parsed.get("frequency", "daily 9:00")),
        )

        logger.info(
            "NLProgrammer: создан BackgroundGoal %s для user=%s",
            goal.id,
            user.telegram_id,
        )
        return goal

    @staticmethod
    def _extract_json(text: str) -> str:
        """Извлечь JSON-блок из текста LLM-ответа.

        Обрабатывает случаи:
        - Чистый JSON: ``{"description": "..."}``
        - JSON в markdown-блоке: ```json ... ```
        - JSON в тексте: найти первый { и последний }

        Args:
            text: Сырой ответ LLM.

        Returns:
            Строка с JSON (может быть невалидной — валидация выше).
        """
        text = text.strip()

        # Убрать markdown-обёртку ```json ... ```
        if text.startswith("```"):
            lines = text.split("\n")
            # Убрать первую строку (```json или ```)
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            # Убрать последнюю строку если это ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # Найти первый { и последний }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]

        return text
