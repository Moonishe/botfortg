"""Proactive Scheduler — управление фоновыми целями по расписанию.

Позволяет регистрировать повторяющиеся цели (BackgroundGoal)
и автоматически запускать их при наступлении времени выполнения.

Использует in-memory хранилище (TODO: DB-backed persistence).
Интегрируется с LLM через router.build_provider для исполнения целей.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Data Classes
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class BackgroundGoal:
    """Повторяющаяся фоновая цель, исполняемая по расписанию.

    Attributes:
        id: Уникальный идентификатор цели.
        user_id: Telegram user_id владельца.
        description: Человеко-читаемое описание цели (что нужно сделать).
        frequency: Строка расписания: "daily 9:00", "weekly monday", "hourly".
        enabled: Включена ли цель.
        last_run: Время последнего запуска (UTC).
        next_run: Время следующего запуска (UTC).
    """

    id: str
    user_id: int
    description: str
    frequency: str
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None


# ══════════════════════════════════════════════════════════════════════════
# Frequency Parser
# ══════════════════════════════════════════════════════════════════════════

# Дни недели: lowercase English → ISO weekday (1=Monday, 7=Sunday)
_WEEKDAY_MAP: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "четверг": 3,
    "пятница": 4,
    "суббота": 5,
    "воскресенье": 6,
}


def _parse_frequency(freq: str) -> timedelta | None:
    """Разобрать строку частоты в timedelta до следующего запуска.

    Поддерживаемые форматы:
        "hourly"          — каждый час от текущего момента
        "daily HH:MM"     — каждый день в указанное время (UTC)
        "weekly WEEKDAY"  — раз в неделю в указанный день в 9:00 UTC
        "weekly"          — каждую неделю, понедельник 9:00 UTC

    Возвращает timedelta от now до следующего запуска, или None если
    формат не распознан.
    """
    now = datetime.now(timezone.utc)
    freq_lower = freq.strip().lower()

    if freq_lower == "hourly":
        # Следующий запуск — ровно через час
        return timedelta(hours=1)

    if freq_lower.startswith("daily"):
        # "daily 9:00" или "daily 09:00"
        parts = freq_lower.split()
        hour, minute = 9, 0
        if len(parts) >= 2:
            time_part = parts[1]
            try:
                h, m = time_part.split(":")
                hour, minute = int(h), int(m)
            except (ValueError, IndexError):
                hour, minute = 9, 0

        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run - now

    if freq_lower.startswith("weekly"):
        # "weekly monday" или просто "weekly"
        parts = freq_lower.split()
        target_day = 0  # Monday by default
        if len(parts) >= 2 and parts[1] in _WEEKDAY_MAP:
            target_day = _WEEKDAY_MAP[parts[1]]

        current_day = now.weekday()  # Monday=0, Sunday=6
        days_ahead = target_day - current_day
        if days_ahead < 0:
            days_ahead += 7
        elif days_ahead == 0:
            # Тот же день — проверяем, не прошло ли уже запланированное время
            target_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if now >= target_time:
                days_ahead += 7  # Уже прошло — переносим на следующую неделю

        next_run = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(
            days=days_ahead
        )
        return next_run - now

    logger.warning("ProactiveScheduler: неизвестный формат частоты: %r", freq)
    return None


# ══════════════════════════════════════════════════════════════════════════
# Proactive Scheduler
# ══════════════════════════════════════════════════════════════════════════


class ProactiveScheduler:
    """Управляет фоновыми целями, исполняемыми по расписанию.

    Хранит цели в памяти. При вызове run_due() находит все цели,
    время которых наступило, и последовательно исполняет их через LLM.

    TODO: DB-backed persistence для сохранения целей между перезапусками.
    """

    def __init__(self) -> None:
        self._goals: dict[str, BackgroundGoal] = {}
        self._lock = asyncio.Lock()

    async def register(self, goal: BackgroundGoal) -> None:
        """Зарегистрировать новую или обновить существующую цель."""
        async with self._lock:
            if goal.next_run is None:
                delta = _parse_frequency(goal.frequency)
                if delta is not None:
                    goal.next_run = datetime.now(timezone.utc) + delta
            self._goals[goal.id] = goal
            logger.info(
                "ProactiveScheduler: зарегистрирована цель %r (frequency=%s, next=%s)",
                goal.id,
                goal.frequency,
                goal.next_run.isoformat() if goal.next_run else "N/A",
            )

    async def unregister(self, goal_id: str) -> bool:
        """Удалить цель по идентификатору. Возвращает True если цель существовала."""
        async with self._lock:
            existed = goal_id in self._goals
            self._goals.pop(goal_id, None)
            if existed:
                logger.info("ProactiveScheduler: цель %r удалена", goal_id)
            return existed

    async def get_due_goals(self) -> list[BackgroundGoal]:
        """Получить список целей, время которых наступило."""
        now = datetime.now(timezone.utc)
        async with self._lock:
            return [
                g
                for g in self._goals.values()
                if g.enabled and g.next_run is not None and g.next_run <= now
            ]

    async def run_due(self, session, user) -> list[dict]:
        """Исполнить все цели, время которых наступило.

        Для каждой цели:
        1. Вызывает LLM для генерации действия по описанию цели.
        2. Отправляет результат через notification_queue.
        3. Обновляет last_run и вычисляет next_run.

        Возвращает список словарей с результатами исполнения:
            [{"goal_id": str, "success": bool, "output": str}, ...]
        """
        due = await self.get_due_goals()
        if not due:
            return []

        results: list[dict] = []
        for goal in due:
            result = {"goal_id": goal.id, "success": False, "output": ""}
            try:
                output = await self._execute_goal(goal, session, user)
                result["success"] = True
                result["output"] = output
                logger.info(
                    "ProactiveScheduler: цель %r выполнена — %s",
                    goal.id,
                    output[:100] if output else "(empty)",
                )
                # Обновить тайминг только при успешном выполнении
                async with self._lock:
                    goal.last_run = datetime.now(timezone.utc)
                    delta = _parse_frequency(goal.frequency)
                    if delta is not None:
                        goal.next_run = goal.last_run + delta
            except Exception:
                logger.exception(
                    "ProactiveScheduler: ошибка выполнения цели %r", goal.id
                )
                result["output"] = "ошибка выполнения"

            results.append(result)

        return results

    async def _execute_goal(self, goal: BackgroundGoal, session, user) -> str:
        """Исполнить одну цель через LLM.

        Строит промпт с контекстом пользователя, отправляет в LLM,
        возвращает текстовый результат.
        """
        from src.llm.router import build_provider
        from src.llm.base import TaskType

        provider = await build_provider(session, user, task_type=TaskType.BACKGROUND)
        if not provider:
            logger.warning(
                "ProactiveScheduler: нет доступного провайдера для цели %r", goal.id
            )
            return "⚠️ нет доступной LLM"

        # Собираем контекст: последние факты о пользователе
        context_lines: list[str] = []
        try:
            from src.db.repo import list_memories

            memories = await list_memories(session, user, limit=10)
            if memories:
                context_lines.append("📋 Последние факты о пользователе:")
                for m in memories[:5]:
                    context_lines.append(f"  • {m.fact[:200]}")
        except Exception:
            pass  # контекст опционален

        prompt = (
            f"🎯 **Фоновая цель:** {goal.description}\n\n"
            + ("\n".join(context_lines) + "\n\n" if context_lines else "")
            + (
                "Ты — персональный AI-ассистент. Выполни указанную выше цель. "
                "Будь конкретным, полезным и лаконичным. "
                "Если нужна дополнительная информация — укажи что именно. "
                "Отвечай на русском языке."
            )
        )

        try:
            from src.llm.base import ChatMessage

            response = await provider.chat(
                [ChatMessage(role="user", content=prompt)],
                task_type=TaskType.BACKGROUND,
            )
            return response
        except Exception as e:
            logger.error("ProactiveScheduler: ошибка LLM для цели %r: %s", goal.id, e)
            return f"⚠️ ошибка LLM: {e}"

    async def get_status(self) -> dict:
        """Получить статус всех зарегистрированных целей."""
        async with self._lock:
            goals_info = []
            for g in self._goals.values():
                goals_info.append(
                    {
                        "id": g.id,
                        "description": g.description[:100],
                        "frequency": g.frequency,
                        "enabled": g.enabled,
                        "last_run": g.last_run.isoformat() if g.last_run else None,
                        "next_run": g.next_run.isoformat() if g.next_run else None,
                    }
                )
            return {"total": len(self._goals), "goals": goals_info}


# ── Глобальный singleton ─────────────────────────────────────────────
proactive_scheduler = ProactiveScheduler()
