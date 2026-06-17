"""Scheduling: background tasks, notifications, periodic work.

Включает:
- Специализированные планировщики (digest, news, reminders, и т.д.)
- Generic Cron Scheduler (cron/) — повторяющиеся задачи с cron-выражениями
"""

# Импорт cron-планировщика обеспечивает регистрацию @task_manager.task
# при загрузке пакета.
from src.core.scheduling.cron.scheduler import cron_scheduler

# Импорт MCP-инструментов обеспечивает регистрацию @tool декораторов.
# Инструменты регистрируются автоматически при импорте модуля.
import src.core.actions.mcp_cron  # noqa: F401 — side-effect: регистрация @tool

__all__ = [
    "cron_scheduler",
]
