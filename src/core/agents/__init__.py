"""Agent Runtime — автономное исполнение планов с чекпоинтами и бюджетом токенов.

Модуль предоставляет:
- ``AgentRuntime`` — движок пошагового исполнения планов с чекпоинтингом.
- ``AgentCheckpoint`` — снапшот состояния агента для возобновления.
- ``TokenBudget`` — контроль расхода токенов.
- ``AgentState`` — состояние исполнения (шаги, история, ошибки).
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
]
