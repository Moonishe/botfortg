from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentTask:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent_type: str = ""
    system_prompt: str = ""
    user_prompt: str = ""
    context: dict = field(default_factory=dict)
    cache_ttl: int = 0  # секунд, 0 = не кэшировать


@dataclass
class AgentResult:
    task_id: str = ""
    agent_type: str = ""
    success: bool = False
    data: dict = field(default_factory=dict)
    tokens_used: int = 0
    cache_key: str | None = None
    elapsed_ms: float = 0.0
    error: str | None = None
