"""Generic Cron Scheduler — гибкая система повторяющихся задач.

Компоненты:
- parser.py — NL → cron expression парсер (croniter + LLM fallback)
- scheduler.py — Ядро: asyncio loop, tick, dispatch
- delivery.py — Система доставки (Telegram / userbot / notification_queue)
- blueprints.py — Каталог готовых шаблонов расписаний
"""
