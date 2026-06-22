"""Command: /ops - compact operational dashboard."""

from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOnly

logger = logging.getLogger(__name__)

router = Router(name="ops_cmd")
router.message.filter(OwnerOnly())


@router.message(Command("ops"))
async def cmd_ops(message: Message) -> None:
    """Show one compact dashboard for runtime health and action priorities."""
    telegram_id = message.from_user.id if message.from_user else 0
    snapshot = await collect_ops_snapshot(telegram_id)
    await message.answer(format_ops_snapshot(snapshot))


async def collect_ops_snapshot(telegram_id: int) -> dict[str, Any]:
    """Collect a best-effort runtime snapshot for /ops."""
    keys = (
        "db",
        "tasks",
        "security",
        "caches",
        "circuits",
        "tools",
        "responses",
        "memory_queue",
    )
    results = await asyncio.gather(
        _collect_db(telegram_id),
        _collect_tasks(),
        _collect_security(),
        _collect_caches(),
        _collect_circuits(),
        _collect_tools(),
        _collect_responses(),
        _collect_memory_queue(),
        return_exceptions=True,
    )
    snapshot: dict[str, Any] = {}
    for key, result in zip(keys, results, strict=True):
        if isinstance(result, Exception):
            logger.error(
                "ops collector failed: %s",
                key,
                exc_info=(type(result), result, result.__traceback__),
            )
            snapshot[key] = {"error": result.__class__.__name__}
        else:
            snapshot[key] = result
    return snapshot


async def _collect_db(telegram_id: int) -> dict[str, Any]:
    if telegram_id <= 0:
        return {"ok": False, "error": "missing_user"}

    from src.config import settings
    from src.db.repo import get_user_by_telegram_id
    from src.db.session import get_session

    try:
        async with get_session() as session:
            await get_user_by_telegram_id(session, telegram_id)
    except Exception as exc:
        logger.debug("ops db check failed", exc_info=True)
        return {"ok": False, "error": exc.__class__.__name__}

    db_path: Path = settings.data_dir / "app.db"
    size_mb = round(db_path.stat().st_size / 1024 / 1024, 1) if db_path.exists() else 0
    return {"ok": True, "size_mb": size_mb}


async def _collect_tasks() -> dict[str, Any]:
    from src.core.infra.task_manager import task_manager

    statuses = task_manager.status()
    failed: list[str] = []
    running = 0
    total_failures = 0
    for name, status in statuses.items():
        if status.get("running"):
            running += 1
        failures = int(status.get("failures") or 0)
        total_failures += failures
        if status.get("status") == "failed" or failures > 0:
            failed.append(name)
    return {
        "total": len(statuses),
        "running": running,
        "failed": sorted(failed),
        "failures": total_failures,
    }


async def _collect_security() -> dict[str, Any]:
    from src.core.security.audit import SecurityAuditor

    report = await SecurityAuditor().run()
    counts = {"ok": 0, "warning": 0, "critical": 0, "info": 0}
    for finding in report.findings:
        counts[finding.status] = counts.get(finding.status, 0) + 1
    return {"overall": report.overall, "counts": counts}


async def _collect_caches() -> dict[str, Any]:
    from src.core.cache.manager import cache_manager

    stats = await cache_manager.all_stats()
    pressure: list[dict[str, Any]] = []
    for name, item in stats.items():
        size = int(item.get("size") or 0)
        max_size = int(item.get("max_size") or 0)
        if max_size > 0 and size / max_size >= 0.8:
            pressure.append({"name": name, "size": size, "max_size": max_size})
    return {"total": len(stats), "pressure": pressure}


async def _collect_circuits() -> dict[str, Any]:
    from src.core.observability.circuit_telemetry import circuit_telemetry

    report = await circuit_telemetry.get_report()
    states = {"open": 0, "half_open": 0, "closed": 0}
    for item in report.get("circuits", {}).values():
        state = str(item.get("current_state") or "CLOSED").lower()
        if state == "open":
            states["open"] += 1
        elif state == "half_open":
            states["half_open"] += 1
        else:
            states["closed"] += 1
    return {"total": int(report.get("total_circuits") or 0), "states": states}


async def _collect_tools() -> dict[str, Any]:
    from src.core.observability.tool_metrics import tool_metrics

    snapshots = await tool_metrics.get_all_snapshots()
    total_calls = sum(s.call_count for s in snapshots)
    total_errors = sum(s.error_count for s in snapshots)
    slow = sorted(snapshots, key=lambda s: s.avg_latency_ms, reverse=True)[:3]
    error_tools = [s for s in snapshots if s.error_count > 0][:3]
    return {
        "tools": len(snapshots),
        "calls": total_calls,
        "errors": total_errors,
        "slow": [
            {
                "name": s.tool_name,
                "avg_ms": round(s.avg_latency_ms, 1),
                "calls": s.call_count,
            }
            for s in slow
        ],
        "error_tools": [
            {"name": s.tool_name, "errors": s.error_count, "calls": s.call_count}
            for s in error_tools
        ],
    }


async def _collect_responses() -> dict[str, Any]:
    from src.core.observability.response_trace import get_response_trace_metrics

    return get_response_trace_metrics()


async def _collect_memory_queue() -> dict[str, Any]:
    from src.core.memory._queue_core import get_queue_stats

    return await get_queue_stats()


def format_ops_snapshot(snapshot: dict[str, Any]) -> str:
    """Format an ops snapshot as HTML-safe Telegram text."""
    lines: list[str] = ["<b>Ops Dashboard</b>", ""]
    lines.extend(_format_runtime(snapshot))
    lines.append("")
    lines.extend(_format_quality(snapshot))
    lines.append("")
    lines.extend(_format_alerts(snapshot))
    return "\n".join(lines)


def _format_runtime(snapshot: dict[str, Any]) -> list[str]:
    db = snapshot.get("db", {})
    tasks = snapshot.get("tasks", {})
    queue = snapshot.get("memory_queue", {})
    caches = snapshot.get("caches", {})

    db_text = "OK" if db.get("ok") else f"DOWN ({_safe(db.get('error'))})"
    if db.get("ok"):
        db_text += f", {db.get('size_mb', 0)} MB"

    failed_tasks = tasks.get("failed") or []
    task_text = f"{tasks.get('running', 0)}/{tasks.get('total', 0)} running"
    if failed_tasks:
        task_text += f", failed: {_safe(', '.join(failed_tasks[:3]))}"

    queue_text = (
        f"{queue.get('size', 0)}/{queue.get('max_size', 0)}"
        f", DLQ {queue.get('dlq_size', 0)}/{queue.get('dlq_max_size', 0)}"
    )

    pressure = caches.get("pressure") or []
    cache_text = f"{caches.get('total', 0)} caches"
    if pressure:
        names = ", ".join(_safe(p["name"]) for p in pressure[:3])
        cache_text += f", pressure: {names}"

    return [
        "<b>Runtime</b>",
        f"DB: {db_text}",
        f"Tasks: {task_text}",
        f"Memory queue: {queue_text}",
        f"Caches: {cache_text}",
    ]


def _format_quality(snapshot: dict[str, Any]) -> list[str]:
    security = snapshot.get("security", {})
    circuits = snapshot.get("circuits", {})
    tools = snapshot.get("tools", {})
    responses = snapshot.get("responses", {})

    counts = security.get("counts") or {}
    security_text = (
        f"{_safe(security.get('overall', 'unknown'))} "
        f"(crit {counts.get('critical', 0)}, warn {counts.get('warning', 0)})"
    )

    states = circuits.get("states") or {}
    circuit_text = (
        f"{circuits.get('total', 0)} total, "
        f"open {states.get('open', 0)}, half-open {states.get('half_open', 0)}"
    )

    tool_text = (
        f"{tools.get('tools', 0)} tools, {tools.get('calls', 0)} calls, "
        f"{tools.get('errors', 0)} errors"
    )

    lines = [
        "<b>Quality</b>",
        f"Security: {security_text}",
        f"Circuits: {circuit_text}",
        f"Tool metrics: {tool_text}",
    ]
    response_routes = responses.get("routes") or []
    if response_routes:
        slowest = response_routes[0]
        lines.append(
            "Responses: "
            f"{responses.get('total_calls', 0)} calls, "
            f"slowest {_safe(slowest['route'])} "
            f"{slowest['avg_ms']}ms avg"
        )
    slow = tools.get("slow") or []
    if slow:
        parts = [
            f"{_safe(item['name'])} {item['avg_ms']}ms/{item['calls']}x"
            for item in slow
        ]
        lines.append(f"Slow tools: {', '.join(parts)}")
    return lines


def _format_alerts(snapshot: dict[str, Any]) -> list[str]:
    alerts = ops_alerts_for_snapshot(snapshot)
    lines = ["<b>Immediate ops alerts</b>"]
    if not alerts:
        lines.append("No immediate alerts.")
        return lines
    lines.extend(f"- {_safe(alert)}" for alert in alerts)
    return lines


def ops_alerts_for_snapshot(snapshot: dict[str, Any]) -> list[str]:
    """Return action-worthy alerts from an ops snapshot."""
    return _ops_alerts(snapshot)


def _ops_alerts(snapshot: dict[str, Any]) -> list[str]:
    alerts: list[str] = []
    for section, value in snapshot.items():
        if isinstance(value, dict) and value.get("error") and section != "db":
            alerts.append(f"Сборщик {section} ошибся: {value['error']}")

    db = snapshot.get("db", {})
    if not db.get("ok"):
        alerts.append(f"Проверка БД не прошла: {db.get('error', 'неизвестно')}")

    security = snapshot.get("security", {})
    if security.get("overall") == "critical":
        alerts.append("Аудит безопасности: критические проблемы")
    elif security.get("overall") == "warning":
        alerts.append("Аудит безопасности: предупреждения")

    tasks = snapshot.get("tasks", {})
    failed_tasks = tasks.get("failed") or []
    if failed_tasks:
        alerts.append(f"Фоновые задачи требуют внимания: {', '.join(failed_tasks[:3])}")

    circuits = snapshot.get("circuits", {})
    states = circuits.get("states") or {}
    if states.get("open", 0) > 0:
        alerts.append(f"{states['open']} предохранитель(ей) разомкнут")

    queue = snapshot.get("memory_queue", {})
    size = int(queue.get("size") or 0)
    max_size = int(queue.get("max_size") or 0)
    if max_size > 0 and size / max_size >= 0.8:
        alerts.append("Очередь памяти почти заполнена")
    if int(queue.get("dlq_size") or 0) > 0:
        alerts.append("В очереди памяти есть ожидающие задания (DLQ)")

    caches = snapshot.get("caches", {})
    if caches.get("pressure"):
        alerts.append("Некоторые кэши почти заполнены")

    tools = snapshot.get("tools", {})
    if int(tools.get("errors") or 0) > 0:
        alerts.append("В метриках инструментов есть ошибки")
    return alerts


def _safe(value: Any) -> str:
    return html.escape(str(value), quote=False)
