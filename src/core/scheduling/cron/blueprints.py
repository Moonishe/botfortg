"""Blueprint Catalog — готовые шаблоны cron-задач.

Каждый blueprint — это предустановленная конфигурация cron-задачи,
которую пользователь может активировать одной командой.

Формат:
    name: str — название
    description: str — описание
    cron_expression: str — cron-выражение
    payload_type: str — тип действия
    payload: dict — параметры действия
    channel: str — канал доставки
    tags: list[str] — теги для категоризации
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CronBlueprint:
    """Шаблон cron-задачи."""

    name: str
    description: str
    cron_expression: str
    payload_type: str = "message"
    payload: dict[str, Any] = field(default_factory=dict)
    channel: str = "notification_queue"
    tags: list[str] = field(default_factory=list)


# ── Каталог шаблонов ───────────────────────────────────────────────────────

BLUEPRINTS: list[CronBlueprint] = [
    CronBlueprint(
        name="Утренняя сводка",
        description="Ежедневный дайджест: погода, события, напоминания",
        cron_expression="0 8 * * *",
        payload_type="llm_prompt",
        payload={
            "prompt": (
                "Составь утреннюю сводку: сегодняшняя дата, "
                "ключевые события из заметок, напоминания на сегодня."
            ),
        },
        tags=["daily", "digest", "morning"],
    ),
    CronBlueprint(
        name="Вечерний обзор",
        description="Итоги дня: что сделано, что осталось",
        cron_expression="0 21 * * *",
        payload_type="llm_prompt",
        payload={
            "prompt": (
                "Подведи итоги дня: что было сделано, "
                "какие задачи остались, планы на завтра."
            ),
        },
        tags=["daily", "evening", "review"],
    ),
    CronBlueprint(
        name="Напоминание: проверить почту",
        description="Напоминание проверить email каждое утро",
        cron_expression="0 9 * * 1-5",
        payload={
            "text": "📧 Не забудь проверить рабочую почту!",
        },
        tags=["reminder", "work", "morning"],
    ),
    CronBlueprint(
        name="Напоминание: разминка",
        description="Напоминание сделать разминку каждый час",
        cron_expression="0 * * * *",
        payload={
            "text": "🧘‍♂️ Время разминки! Встань, потянись, сделай пару упражнений.",
        },
        tags=["health", "reminder", "hourly"],
    ),
    CronBlueprint(
        name="Напоминание: вода",
        description="Напоминание выпить воды каждые 2 часа",
        cron_expression="0 */2 * * *",
        payload={
            "text": "💧 Не забудь выпить воды!",
        },
        tags=["health", "reminder"],
    ),
    CronBlueprint(
        name="Еженедельный отчёт",
        description="Отчёт о проделанной работе за неделю",
        cron_expression="0 10 * * 1",
        payload_type="llm_prompt",
        payload={
            "prompt": (
                "Составь еженедельный отчёт: что было сделано за неделю, "
                "ключевые достижения, планы на следующую неделю."
            ),
        },
        tags=["weekly", "report", "work"],
    ),
    CronBlueprint(
        name="Медитация",
        description="Напоминание о вечерней медитации",
        cron_expression="0 22 * * *",
        payload={
            "text": "🧘‍♀️ Время вечерней медитации. 10 минут тишины и спокойствия.",
        },
        tags=["health", "evening", "reminder"],
    ),
    CronBlueprint(
        name="Ежемесячный обзор финансов",
        description="Напоминание проверить бюджет в начале месяца",
        cron_expression="0 10 1 * *",
        payload={
            "text": "💰 Начало месяца! Проверь бюджет, оплати счета, спланируй расходы.",
        },
        tags=["monthly", "finance", "reminder"],
    ),
    CronBlueprint(
        name="План на день",
        description="Составление плана на день (рабочие дни)",
        cron_expression="0 8 * * 1-5",
        payload_type="llm_prompt",
        payload={
            "prompt": (
                "Составь план на сегодня: определи 3 главные задачи, "
                "распредели время, учти встречи."
            ),
        },
        tags=["daily", "work", "planning"],
    ),
]


def get_blueprint(name: str) -> CronBlueprint | None:
    """Получить шаблон по имени (case-insensitive)."""
    name_lower = name.lower().strip()
    for bp in BLUEPRINTS:
        if bp.name.lower() == name_lower:
            return bp
    return None


def search_blueprints(query: str) -> list[CronBlueprint]:
    """Поиск шаблонов по названию, описанию и тегам."""
    query_lower = query.lower().strip()
    if not query_lower:
        return list(BLUEPRINTS)

    results: list[CronBlueprint] = []
    for bp in BLUEPRINTS:
        if (
            query_lower in bp.name.lower()
            or query_lower in bp.description.lower()
            or any(query_lower in tag.lower() for tag in bp.tags)
        ):
            results.append(bp)
    return results


def get_blueprints_by_tag(tag: str) -> list[CronBlueprint]:
    """Получить все шаблоны с указанным тегом."""
    tag_lower = tag.lower().strip()
    return [bp for bp in BLUEPRINTS if tag_lower in [t.lower() for t in bp.tags]]
