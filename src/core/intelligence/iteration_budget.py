"""Iteration Budget — бюджет вызовов для контроля ресурсов агента.

Отслеживает количество LLM-вызовов и вызовов инструментов в рамках
скользящего временного окна.  Счётчики — простые int, защищённые GIL,
без asyncio.Lock.

Пример использования::

    budget = IterationBudget(max_llm_calls=20, max_tool_calls=50)

    if not budget.record_llm_call():
        raise RuntimeError("LLM call budget exhausted")

    while budget.remaining_tools() > 0:
        if not budget.record_tool_call():
            break
        ...

    budget.reset()
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class IterationBudget:
    """Бюджет вызовов LLM и инструментов в скользящем окне.

    Атрибуты:
        max_llm_calls:  Максимальное количество LLM-вызовов.
        max_tool_calls: Максимальное количество tool-вызовов.
        window_seconds: Размер скользящего окна в секундах.
    """

    max_llm_calls: int = 20
    max_tool_calls: int = 50
    window_seconds: float = 60.0

    # ── Внутренние счётчики (int, GIL-protected) ────────────────
    _llm_calls: int = field(default=0, init=False, repr=False)
    _tool_calls: int = field(default=0, init=False, repr=False)
    _window_start: float = field(default_factory=time.monotonic, init=False, repr=False)

    # ── LLM calls ─────────────────────────────────────────────────

    def record_llm_call(self) -> bool:
        """Зарегистрировать LLM-вызов.

        При превышении ``max_llm_calls`` в текущем окне НЕ увеличивает
        счётчик и возвращает False.

        Returns:
            True если вызов разрешён, False если бюджет исчерпан.
        """
        self._maybe_rotate_window()
        if self._llm_calls >= self.max_llm_calls:
            logger.warning(
                "LLM call budget exhausted: %d/%d",
                self._llm_calls,
                self.max_llm_calls,
            )
            return False
        self._llm_calls += 1
        return True

    def record_tool_call(self) -> bool:
        """Зарегистрировать tool-вызов.

        При превышении ``max_tool_calls`` в текущем окне НЕ увеличивает
        счётчик и возвращает False.

        Returns:
            True если вызов разрешён, False если бюджет исчерпан.
        """
        self._maybe_rotate_window()
        if self._tool_calls >= self.max_tool_calls:
            logger.warning(
                "Tool call budget exhausted: %d/%d",
                self._tool_calls,
                self.max_tool_calls,
            )
            return False
        self._tool_calls += 1
        return True

    # ── Остаток ───────────────────────────────────────────────────

    def remaining_llm(self) -> int:
        """Оставшееся количество LLM-вызовов в текущем окне."""
        self._maybe_rotate_window()
        return max(0, self.max_llm_calls - self._llm_calls)

    def remaining_tools(self) -> int:
        """Оставшееся количество tool-вызовов в текущем окне."""
        self._maybe_rotate_window()
        return max(0, self.max_tool_calls - self._tool_calls)

    # ── Сброс ─────────────────────────────────────────────────────

    def reset(self) -> None:
        """Полный сброс счётчиков и окна."""
        self._llm_calls = 0
        self._tool_calls = 0
        self._window_start = time.monotonic()
        logger.debug("IterationBudget reset")

    # ── Внутренние ────────────────────────────────────────────────

    def _maybe_rotate_window(self) -> None:
        """Проверить скользящее окно; если истекло — сбросить."""
        now = time.monotonic()
        if now - self._window_start >= self.window_seconds:
            self._llm_calls = 0
            self._tool_calls = 0
            self._window_start = now
            logger.debug("IterationBudget window rotated")

    @property
    def exhausted(self) -> bool:
        """True если оба бюджета исчерпаны."""
        return self.remaining_llm() <= 0 and self.remaining_tools() <= 0

    @property
    def llm_exhausted(self) -> bool:
        """True если бюджет LLM-вызовов исчерпан."""
        return self.remaining_llm() <= 0

    @property
    def tools_exhausted(self) -> bool:
        """True если бюджет tool-вызовов исчерпан."""
        return self.remaining_tools() <= 0
