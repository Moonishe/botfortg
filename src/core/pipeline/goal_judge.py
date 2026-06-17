"""Goal Judge — финальная оценка достижения цели.

Используется в Zero-Risk Pipeline для независимой проверки результата.
Возвращает GoalVerdict с вердиктом ok/impossible/reason + confidence.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

from src.core.infra.text_sanitizer import sanitize_html
from src.llm.base import ChatMessage, TaskType

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.db.models import User

logger = logging.getLogger("pipeline.goal_judge")

# ═══════════════════════════════════════════════════════════════════════
# GoalVerdict — модель результата
# ═══════════════════════════════════════════════════════════════════════


class GoalVerdict(BaseModel):
    """Вердикт Goal Judge — ok, impossible, reason, confidence."""

    ok: bool
    impossible: bool
    reason: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @model_validator(mode="after")
    def check_ok_impossible_mutually_exclusive(self) -> GoalVerdict:
        """ok и impossible не могут быть True одновременно."""
        if self.ok and self.impossible:
            raise ValueError("ok and impossible cannot both be True")
        return self

    def is_goal_achieved(self) -> bool:
        """Удобный метод: цель достигнута?"""
        return self.ok and not self.impossible


# ═══════════════════════════════════════════════════════════════════════
# GoalJudge — абстрактный базовый класс
# ═══════════════════════════════════════════════════════════════════════


class GoalJudge(ABC):
    """Абстрактный Goal Judge.

    Реализации должны предоставить асинхронный метод judge(),
    принимающий текстовую цель и транскрипт сообщений.
    """

    @abstractmethod
    async def judge(self, goal: str, transcript: list[ChatMessage]) -> GoalVerdict:
        """Оценить, достигнута ли цель на основе транскрипта."""
        ...


# ═══════════════════════════════════════════════════════════════════════
# GoalJudgeLLM — реализация через LLM-провайдера
# ═══════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = (
    "Ты — Goal Judge. Твоя задача: оценить, достигнута ли поставленная цель "
    "на основе транскрипта взаимодействия. "
    "Верни СТРОГО JSON-объект с полями:\n"
    '  "ok": bool — цель выполнена успешно,\n'
    '  "impossible": bool — цель принципиально невыполнима,\n'
    '  "reason": str (1-2000 символов) — объяснение,\n'
    '  "confidence": float (0.0–1.0) — уверенность в вердикте.\n'
    "Правила:\n"
    "- ok и impossible НЕ могут быть True одновременно.\n"
    "- Если в транскрипте видны ошибки/блокеры, ставь impossible=True.\n"
    "- Отвечай ТОЛЬКО JSON-объектом, без дополнительного текста."
)

_MAX_RETRIES = 2
_RETRY_DELAY_SEC = 2.0


class GoalJudgeLLM(GoalJudge):
    """Goal Judge на базе LLM-провайдера.

    Использует build_provider(session, user, task_type=TaskType.GOAL_JUDGE)
    для получения провайдера, затем вызывает provider.chat() с промптом.
    При недоступности LLM возвращает fallback-вердикт.
    """

    def __init__(
        self,
        session: AsyncSession,
        user: User,
    ) -> None:
        self._session: AsyncSession = session
        self._user: User = user

    async def _build_messages(
        self, goal: str, transcript: list[ChatMessage]
    ) -> list[ChatMessage]:
        """Собрать сообщения для LLM с XML-тегами и санитайзингом."""
        safe_goal = sanitize_html(goal) or "(пустая цель)"
        safe_transcript_parts: list[str] = []
        for msg in transcript:
            role = msg.role
            content = sanitize_html(msg.content) or "(пусто)"
            safe_transcript_parts.append(f"[{role}]: {content}")

        transcript_text = "\n".join(safe_transcript_parts) or "(пустой транскрипт)"

        user_content = (
            f"<goal>\n{safe_goal}\n</goal>\n\n"
            f"<transcript>\n{transcript_text}\n</transcript>"
        )

        return [
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_content),
        ]

    async def _call_llm(self, messages: list[ChatMessage]) -> str | None:
        """Вызвать LLM через build_provider с retry-логикой."""
        # Lazy import для избежания circular imports
        from src.llm.provider_manager import build_provider

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                provider = await build_provider(
                    self._session,
                    self._user,
                    purpose="goal_judge",
                    task_type=TaskType.GOAL_JUDGE,
                )
                if provider is None:
                    logger.warning(
                        "GoalJudgeLLM: build_provider вернул None (attempt %d/%d)",
                        attempt,
                        _MAX_RETRIES,
                    )
                    if attempt < _MAX_RETRIES:
                        await asyncio.sleep(_RETRY_DELAY_SEC)
                    continue

                response = await provider.chat(
                    messages,
                    task_type=TaskType.GOAL_JUDGE,
                )
                return response

            except Exception:
                logger.exception(
                    "GoalJudgeLLM: ошибка вызова LLM (attempt %d/%d)",
                    attempt,
                    _MAX_RETRIES,
                )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY_SEC)

        return None

    @staticmethod
    def _parse_response(raw: str) -> GoalVerdict | None:
        """Распарсить JSON-ответ LLM в GoalVerdict."""
        raw = raw.strip()
        # Убрать возможные markdown-обёртки ```json ... ```
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("GoalJudgeLLM: невалидный JSON от LLM: %s", raw[:200])
            return None

        # Допустимые ключи
        ok = data.get("ok", False)
        impossible = data.get("impossible", False)
        reason = str(data.get("reason", "No reason provided"))[:2000]
        confidence = float(data.get("confidence", 0.5))

        # Валидация через pydantic
        try:
            return GoalVerdict(
                ok=bool(ok),
                impossible=bool(impossible),
                reason=reason or "No reason provided",
                confidence=max(0.0, min(1.0, confidence)),
            )
        except ValueError as exc:
            logger.warning("GoalJudgeLLM: невалидный вердикт: %s", exc)
            return None

    async def judge(self, goal: str, transcript: list[ChatMessage]) -> GoalVerdict:
        """Оценить достижение цели через LLM.

        При недоступности LLM или ошибках парсинга возвращает
        fallback-вердикт (ok=False, impossible=False).
        """
        try:
            messages = await self._build_messages(goal, transcript)
        except Exception:
            logger.exception("GoalJudgeLLM: ошибка сборки сообщений")
            return GoalVerdict(
                ok=False,
                impossible=False,
                reason="Judge unavailable: failed to build messages",
                confidence=0.0,
            )

        raw_response = await self._call_llm(messages)
        if raw_response is None:
            return GoalVerdict(
                ok=False,
                impossible=False,
                reason="Judge unavailable",
                confidence=0.0,
            )

        verdict = self._parse_response(raw_response)
        if verdict is None:
            return GoalVerdict(
                ok=False,
                impossible=False,
                reason="Judge returned unparseable response",
                confidence=0.0,
            )

        logger.info(
            "GoalJudgeLLM: вердикт ok=%s impossible=%s confidence=%.2f reason=%s",
            verdict.ok,
            verdict.impossible,
            verdict.confidence,
            verdict.reason[:100],
        )
        return verdict


# ═══════════════════════════════════════════════════════════════════════
# Фабрика
# ═══════════════════════════════════════════════════════════════════════


def create_goal_judge(session: AsyncSession, user: User) -> GoalJudgeLLM:
    """Создать экземпляр GoalJudgeLLM.

    Args:
        session: Асинхронная сессия SQLAlchemy.
        user: Пользователь (модель User).

    Returns:
        Готовый к использованию GoalJudgeLLM.
    """
    return GoalJudgeLLM(session, user)
