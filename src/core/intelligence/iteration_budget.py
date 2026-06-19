"""Thread-safe iteration budget — shared between parent and sub-agents.

Prevents infinite tool loops while allowing one grace call after exhaustion.
Replaces the hard ``range(MAX_TOOL_ITERATIONS)`` limit with a flexible
budget that supports ``consume()``, ``refund()``, and a grace-call pattern.

Usage::

    budget = IterationBudget(max_total=settings.max_tool_iterations)
    grace_used = False
    while True:
        if not budget.consume():
            if not grace_used:
                grace_used = True
                logger.info("Grace call after budget exhaustion")
            else:
                return {"final_response": "Бюджет итераций исчерпан"}
        # ... tool loop body ...
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class IterationBudget:
    """Thread-safe shared iteration counter.

    Attributes:
        max_total: Maximum iterations allowed (excluding grace).
        _used: Current consumption count.
        _lock: Thread-safe guard for concurrent consume/refund.
    """

    def __init__(self, max_total: int = 90) -> None:
        if max_total < 1:
            raise ValueError(f"max_total must be >= 1, got {max_total}")
        self._max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    @property
    def remaining(self) -> int:
        """Iterations left before exhaustion."""
        return max(0, self._max_total - self._used)

    def consume(self) -> bool:
        """Try to consume one iteration. Returns False if exhausted."""
        with self._lock:
            if self._used >= self._max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """Refund one iteration.

        ponytail: refund() reserved for future execute_code super-step
        where a single tool call conceptually replaces multiple iterations.
        Currently no TH tool uses this, but the API is ready.
        """
        with self._lock:
            if self._used > 0:
                self._used -= 1

    def reset(self) -> None:
        """Reset budget for a new request window.

        Called at the start of each user request to allow fresh iteration counts.
        """
        with self._lock:
            self._used = 0

    def record_tool_call(self) -> bool:
        """Alias for ``consume()`` used by tool registry."""
        return self.consume()

    def record_llm_call(self) -> bool:
        """Alias for ``consume()`` used by LLM router."""
        return self.consume()

    def __repr__(self) -> str:
        return f"IterationBudget(used={self._used}/{self._max_total})"


def budget_for_complexity(score: float, base: int) -> int:
    """Adjust iteration budget based on HTNPlanner complexity score.

    Args:
        score: 0.0–1.0 from HTNPlanner.estimate_complexity().
        base: Flat default (settings.max_tool_iterations).

    Returns:
        Adjusted budget: low complexity → fewer iterations, high → more.
    """
    if score < 0.3:
        return max(5, int(base * 0.5))
    if score >= 0.6:
        return max(1, int(base * 1.5))
    return max(1, base)
