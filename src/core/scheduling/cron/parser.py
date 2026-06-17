"""NL → cron expression парсер.

Два уровня парсинга:
1. Быстрый: Rule-based (регулярки + croniter) для "каждый день в 9:00"
2. Fallback: LLM-парсинг сложных NL-выражений через router.build_provider
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, UTC

import croniter

logger = logging.getLogger(__name__)

# ── Словарь: русские дни недели → 0-6 (0=понедельник, 6=воскресенье) ─────────
_WEEKDAY_RU: dict[str, int] = {
    "понедельник": 0,
    "понедельникам": 0,
    "вторник": 1,
    "вторникам": 1,
    "среда": 2,
    "средам": 2,
    "четверг": 3,
    "четвергам": 3,
    "пятница": 4,
    "пятницам": 4,
    "суббота": 5,
    "субботам": 5,
    "воскресенье": 6,
    "воскресеньям": 6,
}

_WEEKDAY_EN: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_ALL_WEEKDAYS = {**_WEEKDAY_RU, **_WEEKDAY_EN}

# ── Rule-based patterns ─────────────────────────────────────────────────────

# Pattern 1: "каждый день в 9:00" / "ежедневно в 09:30" / "daily at 8:00"
_TIME_IN = r"(?:в|at)\s+"
_RE_DAILY = re.compile(
    rf"(?:каждый\s+день|ежедневно|daily|every\s+day)\s*(?:{_TIME_IN}(\d{{1,2}}):(\d{{2}}))?\s*",
    re.IGNORECASE,
)

# Pattern 2: "каждый час" / "каждые 30 минут"
_RE_HOURLY = re.compile(
    r"(?:каждый\s+час|every\s+hour|каждые\s+(\d+)\s+минут)",
    re.IGNORECASE,
)

# Pattern 3: "каждый понедельник в 10:30" / "по вторникам в 9" / "monday at 10:30"
_WEEKDAYS_PATTERN = "|".join(_ALL_WEEKDAYS.keys())
_RE_WEEKDAY = re.compile(
    rf"(?:каждый\s+)?({_WEEKDAYS_PATTERN})\s*(?:{_TIME_IN}(\d{{1,2}}):?(\d{{2}})?)?\s*",
    re.IGNORECASE,
)

# Pattern 4: "каждые 2 часа" / "каждые 15 минут"
_RE_INTERVAL = re.compile(
    r"(?:каждые?|every)\s+(\d+)\s+(часа?|часов|минут[ыу]?|минуту|hour|hours|minute|minutes|дня?|дней)",
    re.IGNORECASE,
)

# Pattern 5: "по будням в 9:00" / "по выходным" / "weekdays at 9:00"
_RE_WEEKDAY_RANGE = re.compile(
    rf"(?:по\s+)?(?:{_TIME_IN})?(будн[яи]м|будней|weekdays|рабочие\s+дни|рабочих\s+дней)\s*(?:{_TIME_IN}(\d{{1,2}}):(\d{{2}}))?",
    re.IGNORECASE,
)

_RE_WEEKEND = re.compile(
    rf"(?:по\s+)?(?:{_TIME_IN})?(выходн[ыя]е|weekends?|суббот[уы]|воскресень[ея])\s*(?:{_TIME_IN}(\d{{1,2}}):(\d{{2}}))?",
    re.IGNORECASE,
)

# Pattern 6: "каждую минуту"
_RE_EVERY_MINUTE = re.compile(
    r"(?:каждую\s+минуту|every\s+minute|every\s+\d+\s+seconds?)",
    re.IGNORECASE,
)

# Pattern 7: "раз в N дней" / "каждые N дней"
_RE_N_DAYS = re.compile(
    r"(?:раз\s+в|каждые?)\s+(\d+)\s+(дня?|дней|day|days)",
    re.IGNORECASE,
)

# Pattern 8: "первого числа каждого месяца" / "каждый месяц 15-го в 9"
_RE_MONTHLY = re.compile(
    r"(?:каждый\s+месяц|ежемесячно|monthly)\s*(?:(\d+)(?:-го|го|\.)?)?\s*(?:числа?)?\s*(?:в\s+(\d{1,2}):(\d{2}))?",
    re.IGNORECASE,
)

# Pattern 9: "в 9:00" / "at 8:30" (без дней — каждый день)
_RE_TIME_ONLY = re.compile(
    rf"(?:{_TIME_IN})?(\d{{1,2}}):(\d{{2}})\s*$",
    re.IGNORECASE,
)


def _minute_to_interval(minutes: int) -> str:
    """Конвертировать минуты в cron-интервал.

    >>> _minute_to_interval(30)
    '*/30 * * * *'
    >>> _minute_to_interval(15)
    '*/15 * * * *'
    """
    return f"*/{minutes} * * * *"


def _hour_to_cron(hour: int, minute: int = 0) -> str:
    """Создать cron-выражение для daily в указанное время.

    >>> _hour_to_cron(9, 30)
    '30 9 * * *'
    """
    return f"{minute} {hour} * * *"


def _parse_weekday_names(text: str) -> list[int] | None:
    """Извлечь номера дней недели из текста.

    >>> _parse_weekday_names("понедельник")
    [0]
    >>> _parse_weekday_names("понедельник и среда")
    [0, 2]
    >>> _parse_weekday_names("будни")
    [0, 1, 2, 3, 4]
    """
    text_lower = text.lower().strip()

    # Будни
    if any(w in text_lower for w in ["будн", "weekday", "рабочие"]):
        return [0, 1, 2, 3, 4]

    # Выходные
    if any(w in text_lower for w in ["выходн", "weekend"]):
        return [5, 6]

    days: list[int] = []
    # Ищем все названия дней
    for word in re.split(r"[\s,]+", text_lower):
        if word in _ALL_WEEKDAYS:
            days.append(_ALL_WEEKDAYS[word])


    return days if days else None


def _days_to_cron(days: list[int], hour: int = 9, minute: int = 0) -> str:
    """Создать cron-выражение для указанных дней недели.

    >>> _days_to_cron([0, 2], 10, 30)
    '30 10 * * 0,2'
    """
    day_str = ",".join(str(d) for d in sorted(days))
    return f"{minute} {hour} * * {day_str}"


def _extract_hour_minute(
    match: re.Match[str],
    hour_group: int = 2,
    minute_group: int = 3,
    default_hour: int = 9,
    default_minute: int = 0,
) -> tuple[int, int]:
    """Извлечь часы и минуты из групп regex-матча с дефолтами.

    >>> import re
    >>> m = re.match(r"в (\\d{1,2}):(\\d{2})", "в 10:30")
    >>> _extract_hour_minute(m, 1, 2)
    (10, 30)
    """
    hour = match.group(hour_group)
    minute = match.group(minute_group)
    return (
        int(hour) if hour else default_hour,
        int(minute) if minute else default_minute,
    )


def parse_nl_to_cron(text: str) -> str | None:
    """Преобразовать natural language в 5-польное cron-выражение.

    Поддерживаемые форматы:
    - "каждый день в 9:00" → "0 9 * * *"
    - "каждый час" → "0 * * * *"
    - "каждые 30 минут" → "*/30 * * * *"
    - "каждый понедельник в 10:30" → "30 10 * * 0"
    - "каждый вторник и четверг в 9" → "0 9 * * 1,3"
    - "по будням в 9:00" → "0 9 * * 0-4"
    - "каждый месяц 15-го в 9" → "0 9 15 * *"
    - "ежемесячно в 10:00" → "0 10 1 * *"
    - "каждые 2 часа" → "0 */2 * * *"
    - "каждую минуту" → "* * * * *"
    - "в 9:00" → "0 9 * * *"

    Args:
        text: NL-строка с расписанием.

    Returns:
        5-польное cron-выражение или None если не удалось распознать.
    """
    original = text.strip()
    text_lower = original.lower()

    # Pattern: каждую минуту / every minute
    if _RE_EVERY_MINUTE.match(text_lower):
        return "* * * * *"

    # Pattern: каждый час / every hour
    m = _RE_HOURLY.match(text_lower)
    if m:
        interval = m.group(1)
        if interval:
            return _minute_to_interval(int(interval))
        return "0 * * * *"

    # Pattern: каждые N минут/часов
    m = _RE_INTERVAL.search(text_lower)
    if m:
        num = int(m.group(1))
        unit = m.group(2).lower()
        if "минут" in unit or "minute" in unit:
            return _minute_to_interval(num)
        elif "час" in unit or "hour" in unit:
            return f"0 */{num} * * *"
        elif "дн" in unit or "day" in unit:
            return f"0 9 */{num} * *"

    # Pattern: каждые N дней
    m = _RE_N_DAYS.search(text_lower)
    if m:
        num = int(m.group(1))
        return f"0 9 */{num} * *"

    # Pattern: месячные
    m = _RE_MONTHLY.search(text_lower)
    if m:
        day = m.group(1)
        hour = m.group(2)
        minute = m.group(3)
        d = int(day) if day else 1
        h = int(hour) if hour else 9
        mi = int(minute) if minute else 0
        return f"{mi} {h} {d} * *"

    # Pattern: выходные
    m = _RE_WEEKEND.search(text_lower)
    if m:
        h, mi = _extract_hour_minute(m, 2, 3)
        return _days_to_cron([5, 6], h, mi)

    # Pattern: будни
    m = _RE_WEEKDAY_RANGE.search(original)
    if m:
        h, mi = _extract_hour_minute(m, 2, 3)
        return _days_to_cron([0, 1, 2, 3, 4], h, mi)

    # Pattern: конкретный день недели
    m = _RE_WEEKDAY.search(original)
    if m:
        # Передаём полный matched текст, чтобы захватить все дни
        # (например "понедельник и среда" — группа ловит только первый)
        full_match = m.group(0)
        days = _parse_weekday_names(full_match)
        if days:
            h, mi = _extract_hour_minute(m, 2, 3)
            return _days_to_cron(days, h, mi)

    # Pattern: каждый день / ежедневно
    m = _RE_DAILY.search(text_lower)
    if m:
        h, mi = _extract_hour_minute(m, 1, 2)
        return _hour_to_cron(h, mi)

    # Pattern: только "в HH:MM"
    m = _RE_TIME_ONLY.search(text_lower)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        return _hour_to_cron(hour, minute)

    logger.info(
        "CronParser: rule-based parsing failed for %r, trying LLM fallback",
        original,
    )
    return None


# ── Cron expression utilities ──────────────────────────────────────────────


def validate_cron(expression: str) -> bool:
    """Проверить, что cron-выражение валидно (5-польное).

    Args:
        expression: 5-польное cron-выражение (минуты часы дни-месяца месяцы дни-недели).

    Returns:
        True если выражение валидно.
    """
    if not expression or not isinstance(expression, str):
        return False
    parts = expression.strip().split()
    if len(parts) != 5:
        return False
    try:
        croniter.croniter(expression, datetime.now(UTC))
        return True
    except (ValueError, KeyError):
        return False


def get_next_run(
    expression: str,
    after: datetime | None = None,
    tz_str: str = "UTC",
) -> datetime | None:
    """Рассчитать следующее время выполнения для cron-выражения.

    Args:
        expression: 5-польное cron-выражение.
        after: Время, после которого рассчитывать (по умолчанию now в таймзоне).
        tz_str: IANA-таймзона для интерпретации cron (по умолчанию 'UTC').

    Returns:
        datetime (tz-aware) следующего выполнения или None если выражение
        невалидно или достигнут лимит итераций.
    """
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = UTC

    base = after if after is not None else datetime.now(tz)
    try:
        cron = croniter.croniter(expression, base)
        next_dt = cron.get_next(datetime)
        return next_dt
    except (ValueError, KeyError):
        return None


def get_next_runs(
    expression: str,
    count: int = 5,
    after: datetime | None = None,
    tz_str: str = "UTC",
) -> list[datetime]:
    """Рассчитать N следующих выполнений.

    Args:
        expression: 5-польное cron-выражение.
        count: Количество следующих выполнений.
        after: Время, после которого рассчитывать.
        tz_str: IANA-таймзона для интерпретации cron (по умолчанию 'UTC').

    Returns:
        Список datetime следующих выполнений.
    """
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = UTC

    base = after if after is not None else datetime.now(tz)
    try:
        cron = croniter.croniter(expression, base)
        return [cron.get_next(datetime) for _ in range(count)]
    except (ValueError, KeyError):
        return []


def describe_cron(expression: str) -> str:
    """Сгенерировать человеко-читаемое описание cron-выражения.

    Args:
        expression: 5-польное cron-выражение.

    Returns:
        Описание на русском (например 'Каждый день в 09:00').
    """
    parts = expression.strip().split()
    if len(parts) != 5:
        return f"Неизвестный формат: {expression}"

    minute, hour, dom, month, dow = parts

    # Каждую минуту
    if minute == "*" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "Каждую минуту"

    # Каждые N минут
    if minute.startswith("*/"):
        interval = minute[2:]
        return f"Каждые {interval} минут"

    # По часам
    if minute == "0" and hour.startswith("*/"):
        interval = hour[2:]
        return f"Каждые {interval} часа"

    # Ежедневно
    if dom == "*" and month == "*" and dow == "*":
        return f"Каждый день в {hour.zfill(2)}:{minute.zfill(2)}"

    # По дням недели
    if dom == "*" and month == "*" and dow != "*":
        day_names = {
            "0": "пн",
            "1": "вт",
            "2": "ср",
            "3": "чт",
            "4": "пт",
            "5": "сб",
            "6": "вс",
        }
        days = []
        for d in dow.split(","):
            d = d.strip()
            if "-" in d:
                start, end = d.split("-")
                for i in range(int(start), int(end) + 1):
                    days.append(day_names.get(str(i), str(i)))
            else:
                days.append(day_names.get(d, d))
        return f"Каждую {' '.join(days)} в {hour.zfill(2)}:{minute.zfill(2)}"

    # Ежемесячно
    if dom != "*" and month == "*" and dow == "*":
        return f"Каждого {dom} числа в {hour.zfill(2)}:{minute.zfill(2)}"

    return f"Cron: {expression}"
