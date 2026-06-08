"""Agent Runtime — автономное исполнение планов с чекпоинтами и бюджетом токенов.

Модуль предоставляет:
- ``AgentRuntime`` — движок пошагового исполнения планов с чекпоинтингом.
- ``AgentCheckpoint`` — снапшот состояния агента для возобновления.
- ``TokenBudget`` — контроль расхода токенов.
- ``AgentState`` — состояние исполнения (шаги, история, ошибки).
- ``ProactiveScheduler`` — планировщик фоновых целей по расписанию (Phase 4).
- ``BackgroundGoal`` — модель повторяющейся фоновой цели.
"""

from src.core.agents.runtime import (
    AgentCheckpoint,
    AgentRuntime,
    AgentState,
    TokenBudget,
)

__all__ = [
    "AgentCheckpoint",
    "AgentRuntime",
    "AgentState",
    "TokenBudget",
    "BackgroundGoal",
    "ProactiveScheduler",
]


def __getattr__(name: str):
    if name == "BackgroundGoal":
        from src.core.agents.proactive_scheduler import BackgroundGoal

        return BackgroundGoal
    if name == "ProactiveScheduler":
        from src.core.agents.proactive_scheduler import ProactiveScheduler

        return ProactiveScheduler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
