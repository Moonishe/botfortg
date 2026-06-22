"""MCP-инструменты для управления Generic Cron Scheduler.

Включает:
- cron_create — создание cron-задачи
- cron_list — список задач пользователя
- cron_delete — удаление задачи
- cron_update — обновление задачи
- cron_toggle — включить/выключить
- cron_info — детали задачи
- cron_blueprint — создание из шаблона
- cron_blueprint_list — список шаблонов
- cron_parse — парсинг NL → cron
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from src.core.actions.tool_registry import tool
from src.core.scheduling.cron.parser import (
    parse_nl_to_cron,
    validate_cron,
    get_next_run,
    get_next_runs,
    describe_cron,
)
from src.core.scheduling.cron.blueprints import (
    get_blueprint,
    search_blueprints,
    get_blueprints_by_tag,
)
from src.core.scheduling.cron.scheduler import cron_scheduler

logger = logging.getLogger(__name__)

_VALID_CHANNELS = {"notification_queue", "telegram", "userbot"}
_VALID_PAYLOAD_TYPES = {"message", "llm_prompt", "webhook"}


def _resolve_user_id(user_id: int, kwargs: dict[str, Any]) -> int | None:
    """Вернуть user_id, если он совпадает с caller identity из kwargs."""
    caller_id = kwargs.get("user")
    if caller_id is None:
        return user_id
    if int(caller_id) != user_id:
        return None
    return user_id


# ══════════════════════════════════════════════════════════════════════════
# cron_create
# ══════════════════════════════════════════════════════════════════════════


def _parse_tags(tags_str: str | None) -> list[str]:
    """Безопасно распарсить JSON-строку тегов в список.

    Args:
        tags_str: JSON-строка с тегами (или None).

    Returns:
        Список тегов (пустой если невалидно).
    """
    if not tags_str:
        return []
    try:
        parsed: Any = json.loads(tags_str)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def _get_and_check_ownership(
    session, job_id: int, user_id: int
) -> tuple[Any, dict[str, Any] | None]:
    """Получить задачу и проверить владельца.

    Args:
        session: Асинхронная сессия БД.
        job_id: ID задачи.
        user_id: ID пользователя.

    Returns:
        (job, None) — задача найдена и принадлежит пользователю.
        (None, error_dict) — задача не найдена или чужая.
    """
    from src.db.repos.cron_repo import get_cron_job

    job = await get_cron_job(session, job_id)
    if job is None:
        return None, {"success": False, "error": f"Задача #{job_id} не найдена"}
    if job.user_id != user_id:
        return None, {
            "success": False,
            "error": f"Задача #{job_id} принадлежит другому пользователю",
        }
    return job, None


@tool(
    name="cron_create",
    category="scheduling",
    description="Создать новую cron-задачу (расписание, payload, канал доставки).",
    risk="high",
    requires_confirmation=True,
    params={
        "user_id": "int",
        "name": "str",
        "cron_expression_or_nl": "str",
        "payload_type": "str",
        "payload_text": "str|None",
        "description": "str|None",
        "timezone": "str",
        "enabled": "bool",
        "channel": "str",
        "tags": "list[str]|None",
        "max_runs": "int",
    },
)
async def cron_create(
    user_id: int,
    name: str,
    cron_expression_or_nl: str,
    *,
    payload_type: str = "message",
    payload_text: str | None = None,
    description: str | None = None,
    timezone: str = "UTC",
    enabled: bool = True,
    channel: str = "notification_queue",
    tags: list[str] | None = None,
    max_runs: int = 0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Создать новую cron-задачу.

    Args:
        user_id: Telegram ID пользователя-владельца.
        name: Название задачи.
        cron_expression_or_nl: Cron-выражение ('0 9 * * *') или NL
            ('каждый день в 9:00').
        payload_type: 'message' | 'llm_prompt' | 'webhook'.
        payload_text: Текст сообщения (для message) или промпт (для llm_prompt).
        description: Описание задачи.
        timezone: IANA-таймзона.
        enabled: Активна ли задача.
        channel: Канал доставки ('notification_queue', 'telegram', 'userbot').
        tags: Список тегов.
        max_runs: Максимум выполнений (0 = без лимита).

    Returns:
        Результат операции.
    """
    # Валидация обязательных полей

    _uid = _resolve_user_id(user_id, kwargs)
    if _uid is None:
        return {"success": False, "error": "user_id does not match caller identity"}
    user_id = cast(int, _uid)
    if not name or not name.strip():
        return {
            "success": False,
            "error": "Название задачи не может быть пустым.",
        }
    if not cron_expression_or_nl or not cron_expression_or_nl.strip():
        return {
            "success": False,
            "error": "Cron-выражение не может быть пустым.",
        }
    if max_runs < 0:
        return {
            "success": False,
            "error": "max_runs не может быть отрицательным.",
        }
    if payload_type not in _VALID_PAYLOAD_TYPES:
        return {
            "success": False,
            "error": (
                f"Неизвестный payload_type: {payload_type!r}. "
                f"Допустимые: {', '.join(sorted(_VALID_PAYLOAD_TYPES))}."
            ),
        }
    if channel not in _VALID_CHANNELS:
        return {
            "success": False,
            "error": (
                f"Неизвестный канал: {channel!r}. "
                f"Допустимые: {', '.join(sorted(_VALID_CHANNELS))}."
            ),
        }
    # Validate timezone
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(timezone)
    except Exception:
        return {
            "success": False,
            "error": (
                f"Неизвестная таймзона: {timezone!r}. "
                "Используй IANA-название (например, Europe/Moscow, UTC)."
            ),
        }

    # Попытка распарсить NL в cron
    cron_expr = cron_expression_or_nl
    if not validate_cron(cron_expression_or_nl):
        parsed = parse_nl_to_cron(cron_expression_or_nl)
        if parsed and validate_cron(parsed):
            cron_expr = parsed
        else:
            return {
                "success": False,
                "error": (
                    f"Не удалось распарсить cron-выражение: "
                    f"{cron_expression_or_nl!r}. "
                    "Используй 5-польный формат или NL."
                ),
            }

    # Подготовка payload
    payload: dict[str, Any] = {}
    if payload_type == "message":
        if payload_text:
            payload["text"] = payload_text
        else:
            payload["text"] = f"⏰ {name}"
    elif payload_type == "llm_prompt":
        if payload_text:
            payload["prompt"] = payload_text
        else:
            payload["prompt"] = name
    elif payload_type == "webhook":
        if not payload_text or not payload_text.strip():
            return {
                "success": False,
                "error": "Для webhook-задачи payload_text (URL) обязателен.",
            }
        if payload_text:
            # Валидация URL: разрешены только http/https, блокируем
            # внутренние/приватные IP для предотвращения SSRF.
            from urllib.parse import urlparse

            parsed = urlparse(payload_text)
            if parsed.scheme not in ("http", "https"):
                return {
                    "success": False,
                    "error": (
                        "Webhook URL должен использовать http или https, "
                        f"получено: {parsed.scheme!r}"
                    ),
                }
            if not parsed.hostname:
                return {
                    "success": False,
                    "error": "Webhook URL должен содержать hostname",
                }
            # SSRF protection: DNS resolution + IP validation
            from src.core.security.ssrf_guard import _check_ssrf_async

            ssrf_error = await _check_ssrf_async(payload_text)
            if ssrf_error:
                return {"success": False, **ssrf_error}
            payload["url"] = payload_text

    result = await cron_scheduler.create_and_schedule(
        user_id=user_id,
        name=name,
        cron_expression=cron_expr,
        payload_type=payload_type,
        payload=payload,
        description=description,
        timezone=timezone,
        enabled=enabled,
        channel=channel,
        tags=tags,
        max_runs=max_runs,
    )

    if result["success"]:
        desc = describe_cron(cron_expr)
        result["cron_description"] = desc

    return result


# ══════════════════════════════════════════════════════════════════════════
# cron_list
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="cron_list",
    category="scheduling",
    description="Список cron-задач пользователя с фильтрацией.",
)
async def cron_list(
    user_id: int,
    *,
    enabled_only: bool = False,
    tag: str | None = None,
    limit: int = 20,
    **kwargs: Any,
) -> dict[str, Any]:
    """Получить список cron-задач пользователя.

    Args:
        user_id: Telegram ID пользователя.
        enabled_only: Только активные.
        tag: Фильтр по тегу.
        limit: Максимум результатов.

    Returns:
        Список задач.
    """

    _uid = _resolve_user_id(user_id, kwargs)
    if _uid is None:
        return {"success": False, "error": "user_id does not match caller identity"}
    user_id = cast(int, _uid)
    from src.db.repos.cron_repo import list_user_jobs
    from src.db.session import get_session

    async with get_session() as session:
        jobs = await list_user_jobs(
            session, user_id, enabled_only=enabled_only, tag=tag
        )

        jobs_list = []
        for job in jobs[:limit]:
            next_run_str = (
                job.next_run_at.isoformat() if job.next_run_at else "Не запланирована"
            )
            last_run_str = job.last_run_at.isoformat() if job.last_run_at else "Никогда"

            cr_desc = ""
            if job.cron_expression:
                try:
                    cr_desc = describe_cron(job.cron_expression)
                except Exception:
                    cr_desc = job.cron_expression

            tags_parsed = _parse_tags(job.tags)

            jobs_list.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "description": (job.description or "")[:100],
                    "cron": job.cron_expression,
                    "cron_description": cr_desc,
                    "enabled": job.enabled,
                    "payload_type": job.payload_type,
                    "channel": job.channel,
                    "run_count": job.run_count,
                    "max_runs": job.max_runs,
                    "last_run": last_run_str,
                    "next_run": next_run_str,
                    "tags": tags_parsed,
                }
            )

    return {
        "total": len(jobs_list),
        "limit": limit,
        "jobs": jobs_list,
    }


# ══════════════════════════════════════════════════════════════════════════
# cron_delete
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="cron_delete",
    category="scheduling",
    description="Удалить cron-задачу по ID.",
    risk="high",
    requires_confirmation=True,
    params={
        "job_id": "int",
        "user_id": "int",
    },
)
async def cron_delete(job_id: int, user_id: int, **kwargs: Any) -> dict[str, Any]:
    """Удалить cron-задачу.

    Args:
        job_id: ID задачи.
        user_id: ID пользователя (для проверки владельца).

    Returns:
        Результат операции.
    """

    _uid = _resolve_user_id(user_id, kwargs)
    if _uid is None:
        return {"success": False, "error": "user_id does not match caller identity"}
    user_id = cast(int, _uid)
    from src.db.repos.cron_repo import delete_cron_job
    from src.db.session import get_session

    async with get_session() as session:
        job, err = await _get_and_check_ownership(session, job_id, user_id)
        if err:
            return err

        job_name = job.name
        deleted = await delete_cron_job(session, job_id)
        # Примечание: get_session() сам коммитит при выходе из async with

    return {
        "success": deleted,
        "message": f"Задача '{job_name}' (#{job_id}) удалена"
        if deleted
        else "Не найдена",
    }


# ══════════════════════════════════════════════════════════════════════════
# cron_toggle
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="cron_toggle",
    category="scheduling",
    description="Включить или выключить cron-задачу.",
    risk="high",
    requires_confirmation=True,
    params={
        "job_id": "int",
        "user_id": "int",
        "enabled": "bool|None",
    },
)
async def cron_toggle(
    job_id: int, user_id: int, *, enabled: bool | None = None, **kwargs: Any
) -> dict[str, Any]:
    """Переключить статус cron-задачи.

    Args:
        job_id: ID задачи.
        user_id: ID пользователя (для проверки владельца).
        enabled: Новый статус. Если None — инвертировать текущий.

    Returns:
        Результат операции.
    """

    _uid = _resolve_user_id(user_id, kwargs)
    if _uid is None:
        return {"success": False, "error": "user_id does not match caller identity"}
    user_id = cast(int, _uid)
    from src.db.repos.cron_repo import update_cron_job
    from src.db.session import get_session

    async with get_session() as session:
        job, err = await _get_and_check_ownership(session, job_id, user_id)
        if err:
            return err

        job_name = job.name
        new_status = enabled if enabled is not None else not job.enabled
        await update_cron_job(session, job_id, enabled=new_status)
        # Примечание: get_session() сам коммитит при выходе из async with

    status_str = "включена" if new_status else "выключена"
    return {
        "success": True,
        "job_id": job_id,
        "enabled": new_status,
        "message": f"Задача '{job_name}' #{job_id} {status_str}",
    }


# ══════════════════════════════════════════════════════════════════════════
# cron_info
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="cron_info",
    category="scheduling",
    description="Детальная информация о cron-задаче.",
    risk="low",
    requires_confirmation=False,
)
async def cron_info(job_id: int, user_id: int, **kwargs: Any) -> dict[str, Any]:
    """Получить детальную информацию о cron-задаче.

    Args:
        job_id: ID задачи.
        user_id: ID пользователя (для проверки владельца).

    Returns:
        Детали задачи и следующие 5 выполнений.
    """

    _uid = _resolve_user_id(user_id, kwargs)
    if _uid is None:
        return {"success": False, "error": "user_id does not match caller identity"}
    user_id = cast(int, _uid)
    from src.db.session import get_session

    async with get_session() as session:
        job, err = await _get_and_check_ownership(session, job_id, user_id)
        if err:
            return err

        job_data = {
            "id": job.id,
            "name": job.name,
            "description": job.description,
            "cron_expression": job.cron_expression,
            "timezone": job.timezone,
            "enabled": job.enabled,
            "payload_type": job.payload_type,
            "channel": job.channel,
            "run_count": job.run_count,
            "max_runs": job.max_runs,
            "last_run": job.last_run_at.isoformat() if job.last_run_at else None,
            "next_run": job.next_run_at.isoformat() if job.next_run_at else None,
            "tags": _parse_tags(job.tags),
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }

    next_runs = get_next_runs(
        job_data["cron_expression"], count=5, tz_str=job_data["timezone"]
    )

    return {
        "success": True,
        "job": {
            **job_data,
            "cron_description": describe_cron(job_data["cron_expression"]),
        },
        "next_5_runs": [dt.isoformat() for dt in next_runs],
    }


# ══════════════════════════════════════════════════════════════════════════
# cron_blueprint / cron_blueprint_list
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="cron_blueprint_list",
    category="scheduling",
    description="Список доступных шаблонов cron-задач.",
)
async def cron_blueprint_list(
    *, query: str | None = None, tag: str | None = None
) -> dict[str, Any]:
    """Получить список шаблонов cron-задач.

    Args:
        query: Поисковый запрос (по названию, описанию, тегам).
        tag: Фильтр по тегу.

    Returns:
        Список шаблонов.
    """
    if tag:
        blueprints = get_blueprints_by_tag(tag)
    elif query:
        blueprints = search_blueprints(query)
    else:
        from src.core.scheduling.cron.blueprints import BLUEPRINTS

        blueprints = list(BLUEPRINTS)

    return {
        "total": len(blueprints),
        "blueprints": [
            {
                "name": bp.name,
                "description": bp.description,
                "cron": bp.cron_expression,
                "type": bp.payload_type,
                "tags": bp.tags,
            }
            for bp in blueprints
        ],
    }


@tool(
    name="cron_blueprint",
    category="scheduling",
    description="Создать cron-задачу из готового шаблона.",
    risk="high",
    requires_confirmation=True,
)
async def cron_blueprint(
    user_id: int,
    blueprint_name: str,
    *,
    channel: str | None = None,
    timezone: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Создать cron-задачу из шаблона.

    Args:
        user_id: Telegram ID пользователя.
        blueprint_name: Название шаблона (из cron_blueprint_list).
        channel: Переопределить канал доставки.
        timezone: Переопределить таймзону.

    Returns:
        Результат создания задачи.
    """

    _uid = _resolve_user_id(user_id, kwargs)
    if _uid is None:
        return {"success": False, "error": "user_id does not match caller identity"}
    user_id = cast(int, _uid)
    bp = get_blueprint(blueprint_name)
    if bp is None:
        return {
            "success": False,
            "error": (
                f"Шаблон '{blueprint_name}' не найден. Используй cron_blueprint_list."
            ),
        }

    create_kwargs: dict[str, Any] = {
        "user_id": user_id,
        "name": bp.name,
        "cron_expression_or_nl": bp.cron_expression,
        "payload_type": bp.payload_type,
        "payload_text": bp.payload.get("text")
        or bp.payload.get("prompt")
        or bp.payload.get("url"),
        "description": bp.description,
        "tags": bp.tags,
    }
    if channel:
        create_kwargs["channel"] = channel
    if timezone:
        create_kwargs["timezone"] = timezone

    return await cron_create(**create_kwargs)


# ══════════════════════════════════════════════════════════════════════════
# cron_parse
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="cron_parse",
    category="scheduling",
    description="Парсинг NL → cron-выражение (без создания задачи).",
)
async def cron_parse(text: str) -> dict[str, Any]:
    """Распарсить NL-описание в cron-выражение.

    Args:
        text: NL-описание ('каждый день в 9:00', 'по будням в 18:30').

    Returns:
        Результат парсинга с описанием.
    """
    if not text or not text.strip():
        return {"success": False, "error": "Текст не может быть пустым."}
    if len(text) > 500:
        return {
            "success": False,
            "error": "Текст слишком длинный (макс. 500 символов).",
        }
    expr = parse_nl_to_cron(text)
    if expr and validate_cron(expr):
        desc = describe_cron(expr)
        next_run = get_next_run(expr, tz_str="UTC")
        return {
            "success": True,
            "cron_expression": expr,
            "description": desc,
            "next_run": next_run.isoformat() if next_run else None,
        }
    else:
        return {
            "success": False,
            "error": f"Не удалось распарсить: {text!r}",
        }


# ══════════════════════════════════════════════════════════════════════════
# cron_update
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="cron_update",
    category="scheduling",
    description="Обновить параметры существующей cron-задачи.",
    risk="high",
    requires_confirmation=True,
    params={
        "job_id": "int",
        "user_id": "int",
        "name": "str|None",
        "cron_expression": "str|None",
        "enabled": "bool|None",
        "channel": "str|None",
        "payload_text": "str|None",
        "description": "str|None",
        "timezone": "str|None",
        "tags": "list[str]|None",
        "max_runs": "int|None",
    },
)
async def cron_update(
    job_id: int,
    user_id: int,
    *,
    name: str | None = None,
    cron_expression: str | None = None,
    enabled: bool | None = None,
    channel: str | None = None,
    payload_text: str | None = None,
    description: str | None = None,
    timezone: str | None = None,
    tags: list[str] | None = None,
    max_runs: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Обновить параметры cron-задачи.

    Args:
        job_id: ID задачи.
        user_id: ID пользователя (для проверки владельца).
        name: Новое название.
        cron_expression: Новое cron-выражение.
        enabled: Новый статус.
        channel: Новый канал доставки.
        payload_text: Новый текст/промпт.
        description: Новое описание.
        timezone: Новая таймзона.
        tags: Новые теги.
        max_runs: Новый лимит выполнений.

    Returns:
        Результат операции.
    """

    _uid = _resolve_user_id(user_id, kwargs)
    if _uid is None:
        return {"success": False, "error": "user_id does not match caller identity"}
    user_id = cast(int, _uid)
    from src.db.repos.cron_repo import update_cron_job
    from src.db.session import get_session

    # Валидация входных параметров
    if name is not None and (not name or not name.strip()):
        return {"success": False, "error": "Название задачи не может быть пустым."}
    if max_runs is not None and max_runs < 0:
        return {"success": False, "error": "max_runs не может быть отрицательным."}
    if channel is not None and channel not in _VALID_CHANNELS:
        return {
            "success": False,
            "error": (
                f"Неизвестный канал: {channel!r}. "
                f"Допустимые: {', '.join(sorted(_VALID_CHANNELS))}."
            ),
        }
    if timezone is not None:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(timezone)
        except Exception:
            return {
                "success": False,
                "error": (
                    f"Неизвестная таймзона: {timezone!r}. "
                    "Используй IANA-название (например, Europe/Moscow, UTC)."
                ),
            }

    if cron_expression is not None and not cron_expression.strip():
        return {"success": False, "error": "Cron-выражение не может быть пустым."}

    updates: dict[str, Any] = {}

    if name is not None:
        updates["name"] = name.strip()
    if enabled is not None:
        updates["enabled"] = enabled
    if channel is not None:
        updates["channel"] = channel
    if description is not None:
        updates["description"] = description
    if timezone is not None:
        updates["timezone"] = timezone
    if tags is not None:
        updates["tags"] = tags
    if max_runs is not None:
        updates["max_runs"] = max_runs

    # payload_text требует чтения текущего payload из БД —
    # делаем это в одной сессии с апдейтом (избегаем race condition)
    if not updates and payload_text is None and cron_expression is None:
        return {"success": False, "error": "Нет полей для обновления"}

    async with get_session() as session:
        job, err = await _get_and_check_ownership(session, job_id, user_id)
        if err:
            return err

        if cron_expression is not None:
            if not validate_cron(cron_expression):
                parsed = parse_nl_to_cron(cron_expression)
                if parsed and validate_cron(parsed):
                    updates["cron_expression"] = parsed
                    updates["next_run_at"] = get_next_run(
                        parsed, tz_str=timezone or job.timezone
                    )
                else:
                    return {
                        "success": False,
                        "error": f"Невалидное cron-выражение: {cron_expression!r}",
                    }
            else:
                updates["cron_expression"] = cron_expression
                updates["next_run_at"] = get_next_run(
                    cron_expression, tz_str=timezone or job.timezone
                )

        if payload_text is not None:
            if job.payload:
                try:
                    payload = json.loads(job.payload)
                except (json.JSONDecodeError, TypeError):
                    payload = {}
            else:
                payload = {}
            if job.payload_type in ("message", "llm_prompt"):
                if job.payload_type == "message":
                    payload["text"] = payload_text
                else:
                    payload["prompt"] = payload_text
                updates["payload"] = payload
            else:
                return {
                    "success": False,
                    "error": (
                        f"payload_text поддерживается только для message/llm_prompt, "
                        f"не для {job.payload_type!r}"
                    ),
                }

        if not updates:
            return {"success": False, "error": "Нет полей для обновления"}

        updated = await update_cron_job(session, job_id, **updates)
        # Примечание: get_session() сам коммитит при выходе из async with

    return {
        "success": updated is not None,
        "message": f"Задача #{job_id} обновлена",
    }


# Инструменты регистрируются автоматически через @tool декоратор
# при импорте модуля. Никакой ручной регистрации не требуется.
