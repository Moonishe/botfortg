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
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.agents.proactive_scheduler import BackgroundGoal
from src.db.models import User

logger = logging.getLogger(__name__)


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
        from src.llm.base import ChatMessage, TaskType
        from src.llm.router import build_provider

        try:
            provider = await build_provider(
                session, user, purpose="reasoning", task_type=TaskType.BACKGROUND
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

        if not isinstance(parsed, dict) or not parsed.get("description"):
            logger.warning(
                "NLProgrammer: ответ LLM не содержит непустого 'description' (user=%s): %s",
                user.telegram_id,
                parsed,
            )
            return None

        goal = BackgroundGoal(
            id=f"nl_{user.telegram_id}_{uuid.uuid4().hex[:12]}",
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
