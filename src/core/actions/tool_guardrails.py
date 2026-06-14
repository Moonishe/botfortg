"""Tool Guardrails — hash-based loop detection for tool calls.

Detects when the same tool is called with the same parameters within
a sliding window, preventing LLM infinite loops.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_MAX_WINDOW = 20


@dataclass
class LoopResult:
    blocked: bool
    reason: str = ""
    loop_count: int = 0


def _hash_call(tool_name: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tool": tool_name, "params": dict(sorted(params.items()))},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class ToolLoopGuard:
    def __init__(self, max_repeats: int = 3, window_size: int = _MAX_WINDOW) -> None:
        self._max_repeats = max_repeats
        self._window: deque[str] = deque(maxlen=window_size)
        self._counts: dict[str, int] = {}

    def check(self, tool_name: str, params: dict[str, Any]) -> LoopResult:
        h = _hash_call(tool_name, params)
        count = self._counts.get(h, 0)
        if count >= self._max_repeats:
            logger.warning(
                "Tool loop: %r called %d times (hash=%s)", tool_name, count, h[:12]
            )
            return LoopResult(
                blocked=True,
                reason=f"Tool {tool_name!r} called {count}x with same params — loop blocked.",
                loop_count=count,
            )
        return LoopResult(blocked=False)

    def record(self, tool_name: str, params: dict[str, Any]) -> None:
        h = _hash_call(tool_name, params)
        if len(self._window) == self._window.maxlen:
            stale = self._window[0]
            self._counts[stale] = max(0, self._counts.get(stale, 0) - 1)
        self._window.append(h)
        self._counts[h] = self._counts.get(h, 0) + 1

    def reset(self) -> None:
        self._window.clear()
        self._counts.clear()
