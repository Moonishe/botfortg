"""mcp_timer tool — registered via @tool decorator.

Provides in-memory timer and alarm management:

- **set** — create a timer that fires after a given number of seconds.
- **alarm** — create an alarm at a specific HH:MM time (24h).
- **list** — return all active timers with remaining time.
- **cancel** — cancel a timer by its index from the list output.

On fire: sends a notification to the owner via ``notification_queue``.

Persistence is handled via SQLAlchemy ORM (models._timer.Timer table),
migrated via Alembic. On restart, pending timers are reloaded to restore
the ID counter and in-memory state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from src.core.actions.tool_registry import ToolActionSpec, tool
from src.core.security import is_confirmed_truthy
from src.db.models import Notification
from src.db.repo import (
    delete_expired_timers,
    delete_timer,
    list_pending_timers,
    persist_timer,
)

from src.db.session import get_session

logger = logging.getLogger(__name__)

# ── Module-level state ─────────────────────────────────────────────────────

_active_timers: dict[int, dict[str, Any]] = {}
"""Mapping: timer_id -> {task, label, created_at, duration_sec, fire_at}"""
_timer_lock = asyncio.Lock()
_timer_counter = 0
"""Monotonically increasing counter for timer IDs."""

# ── DB load state ──────────────────────────────────────────────────────────

_db_loaded = False
_db_load_lock = asyncio.Lock()

# IDs that were cancelled while their task was about to fire; used to prevent
# a race where a timer task wakes from sleep just after cancellation.
_cancelled_ids: set[int] = set()


async def _ensure_timer_db_loaded() -> None:
    """Load pending timers from ORM, restoring the ID counter and tasks.

    Idempotent and async-safe — only the first call performs the load.
    Expired timers are cleaned up during load.
    """
    global _timer_counter, _db_loaded
    if _db_loaded:
        return

    async with _db_load_lock:
        if _db_loaded:
            return

        async with get_session() as session:
            await delete_expired_timers(session)
            await session.commit()
            timers = await list_pending_timers(session)

        if timers:
            max_tid = max(t.timer_id for t in timers)
            if max_tid >= _timer_counter:
                _timer_counter = max_tid + 1

            now = datetime.now(UTC)
            for t in timers:
                try:
                    fire_dt = datetime.fromisoformat(t.fire_at)
                except ValueError:
                    logger.warning(
                        "Skipping timer %d with invalid fire_at: %r",
                        t.timer_id,
                        t.fire_at,
                    )
                    continue

                remaining = (fire_dt - now).total_seconds()
                if remaining <= 0:
                    continue

                task = asyncio.create_task(
                    _timer_task(t.timer_id, int(remaining), t.label),
                    name=f"restored-timer-{t.timer_id}",
                )
                task.add_done_callback(
                    lambda t, tid=t.timer_id: (
                        logger.exception(
                            "Restored timer task %d failed unexpectedly",
                            tid,
                            exc_info=t.exception(),
                        )
                        if t.exception()
                        and not isinstance(t.exception(), asyncio.CancelledError)
                        else None
                    )
                )
                async with _timer_lock:
                    _active_timers[t.timer_id] = {
                        "task": task,
                        "label": t.label,
                        "created_at": fire_dt.isoformat(),
                        "duration_sec": int(remaining),
                        "fire_at": t.fire_at,
                    }

        _db_loaded = True


# ══════════════════════════════════════════════════════════════════════════
# Tool: mcp_timer
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="mcp_timer",
    description=(
        "Manage in-memory timers and alarms.  Supports four actions:\n"
        "- 'set' — set a timer for a given duration in seconds.\n"
        "- 'alarm' — set an alarm for a specific HH:MM time (24h).\n"
        "- 'list' — list all active timers with remaining time.\n"
        "- 'cancel' — cancel a timer by its index from the list.\n"
        "Timers fire once and send an OS notification."
    ),
    category="utility",
    risk="medium",
    requires_confirmation=True,
    actions={
        "set": ToolActionSpec(
            name="set",
            risk="medium",
            read_only=False,
            destructive=False,
            idempotent=False,
            requires_confirmation=True,
            user_content=True,
        ),
        "alarm": ToolActionSpec(
            name="alarm",
            risk="medium",
            read_only=False,
            destructive=False,
            idempotent=False,
            requires_confirmation=True,
            user_content=True,
        ),
        "list": ToolActionSpec(
            name="list",
            risk="low",
            read_only=True,
            idempotent=True,
            user_content=True,
        ),
        "cancel": ToolActionSpec(
            name="cancel",
            risk="high",
            read_only=False,
            destructive=True,
            idempotent=False,
            requires_confirmation=True,
            user_content=False,
        ),
    },
    params={
        "action": "str — 'set', 'alarm', 'list', or 'cancel'",
        "duration_sec": "int — timer duration in seconds (required for 'set')",
        "label": "str — optional label for the timer/alarm",
        "time": "str — alarm time in HH:MM 24h format (required for 'alarm')",
        "index": "int — timer index from list output (required for 'cancel')",
    },
)
async def mcp_timer(
    action: str,
    duration_sec: int = 0,
    label: str = "",
    time: str = "",
    index: int = -1,
    **kwargs: Any,
) -> dict[str, Any]:
    """In-memory timer and alarm management tool.

    Args:
        action: ``"set"``, ``"alarm"``, ``"list"``, or ``"cancel"``.
        duration_sec: Duration in seconds (required for ``action="set"``).
        label: Optional human-readable label for the timer/alarm.
        time: Time in HH:MM 24h format (required for ``action="alarm"``).
        index: Timer index from ``"list"`` output (required for ``action="cancel"``).

    Returns:
        A dict with the result data or an ``"error"`` key on failure.
    """
    try:
        if action == "set":
            if not is_confirmed_truthy(kwargs.get("_confirmed", False)):
                return {"error": "requires confirmation"}
            return await _timer_set(duration_sec, label)
        elif action == "alarm":
            if not is_confirmed_truthy(kwargs.get("_confirmed", False)):
                return {"error": "requires confirmation"}
            return await _timer_alarm(time, label)
        elif action == "list":
            return await _timer_list()
        elif action == "cancel":
            if not is_confirmed_truthy(kwargs.get("_confirmed", False)):
                return {"error": "requires confirmation"}
            return await _timer_cancel(index)
        else:
            return {
                "error": (
                    f"Unknown action {action!r}. "
                    f"Valid actions: set, alarm, list, cancel"
                )
            }
    except Exception as exc:
        logger.exception("mcp_timer(%r) failed", action)
        return {"error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════
# Action implementations
# ══════════════════════════════════════════════════════════════════════════


async def _timer_set(duration_sec: int, label: str) -> dict[str, Any]:
    """Create a countdown timer."""
    if duration_sec <= 0:
        return {"error": "duration_sec must be a positive integer"}

    await _ensure_timer_db_loaded()

    fire_dt = datetime.now(UTC) + timedelta(seconds=duration_sec)
    lbl = label.strip() if label else f"Timer ({duration_sec}s)"

    return await _start_timer(duration_sec, fire_dt, lbl, "timer")


async def _timer_alarm(time_str: str, label: str) -> dict[str, Any]:
    """Create an alarm for a specific HH:MM time."""
    if not time_str or ":" not in time_str:
        return {"error": "time must be in HH:MM format (e.g. '14:30')"}

    try:
        parts = time_str.strip().split(":")
        if len(parts) != 2:
            raise ValueError
        hour = int(parts[0])
        minute = int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, IndexError):
        return {"error": "time must be in HH:MM 24h format (e.g. '14:30')"}

    await _ensure_timer_db_loaded()

    now = datetime.now(UTC)
    fire_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # If the time has already passed today, schedule for tomorrow
    if fire_dt <= now:
        fire_dt += timedelta(days=1)

    duration_sec = int((fire_dt - now).total_seconds())
    lbl = label.strip() if label else f"Alarm at {time_str}"

    return await _start_timer(duration_sec, fire_dt, lbl, "alarm")


async def _start_timer(
    duration_sec: int,
    fire_dt: datetime,
    label: str,
    task_name_prefix: str,
) -> dict[str, Any]:
    """Create a timer task, persist it, and guard against cancellation races.

    Shared by ``_timer_set`` and ``_timer_alarm``.
    """
    async with _timer_lock:
        global _timer_counter
        _timer_counter += 1
        tid = _timer_counter
        task = asyncio.create_task(
            _timer_task(tid, duration_sec, label),
            name=f"{task_name_prefix}-{tid}",
        )
        task.add_done_callback(
            lambda t, timer_id=tid: (
                logger.exception(
                    "Timer task %d (%s) failed unexpectedly",
                    timer_id,
                    task_name_prefix,
                    exc_info=t.exception(),
                )
                if t.exception()
                and not isinstance(t.exception(), asyncio.CancelledError)
                else None
            )
        )
        _active_timers[tid] = {
            "task": task,
            "label": label,
            "created_at": datetime.now(UTC).isoformat(),
            "duration_sec": duration_sec,
            "fire_at": fire_dt.isoformat(),
        }

    # Guard: if _timer_cancel() interleaves between _timer_lock release
    # and the DB persist, the timer would be popped from _active_timers
    # but the persist below would recreate the DB row — causing the
    # cancelled timer to fire on the next restart.
    async with _timer_lock:
        if tid not in _active_timers:
            return {
                "ok": False,
                "cancelled": True,
                "timer_id": tid,
                "label": label,
                "message": (
                    f"{task_name_prefix.capitalize()} was cancelled before persistence"
                ),
            }

    # Persist atomically inside a single session: if the DB operation fails,
    # we cancel the task and roll back in-memory state. If _timer_cancel()
    # popped the timer between the pre-persist guard and the commit, we
    # rollback the uncommitted INSERT so the cancelled timer never hits disk.
    try:
        async with get_session() as session:
            await persist_timer(session, tid, fire_dt.isoformat(), label)

            async with _timer_lock:
                should_rollback = tid not in _active_timers

            if should_rollback:
                await session.rollback()
                return {
                    "ok": False,
                    "cancelled": True,
                    "timer_id": tid,
                    "label": label,
                    "message": (
                        f"{task_name_prefix.capitalize()} was cancelled "
                        "during persistence"
                    ),
                }

            await session.commit()
    except Exception:
        # DB failure: stop the already-created task and clean up in-memory
        # state so a zombie timer never fires.
        async with _timer_lock:
            _active_timers.pop(tid, None)
            _cancelled_ids.discard(tid)
        if not task.done():
            task.cancel()
        logger.exception("Failed to persist timer %d", tid)
        return {
            "ok": False,
            "error": "Database error: timer could not be persisted",
        }

    return {
        "ok": True,
        "timer_id": tid,
        "label": label,
        "will_fire_at": fire_dt.isoformat(),
        "duration_sec": duration_sec,
    }


async def _timer_list() -> dict[str, Any]:
    """Return all active timers with remaining time."""
    await _ensure_timer_db_loaded()

    now_ts = datetime.now(UTC).timestamp()

    async with _timer_lock:
        snapshot = list(_active_timers.items())

    items: list[dict[str, Any]] = []
    for tid, info in snapshot:
        task = info["task"]
        if task.done():
            continue

        fire_ts = datetime.fromisoformat(info["fire_at"]).timestamp()
        remaining = max(0, int(fire_ts - now_ts))

        items.append(
            {
                "index": tid,
                "label": info["label"],
                "remaining_sec": remaining,
                "fire_at": info["fire_at"],
            }
        )

    return {
        "ok": True,
        "timers": items,
        "count": len(items),
    }


async def _timer_cancel(timer_id: int) -> dict[str, Any]:
    """Cancel a timer by its ID/index."""
    await _ensure_timer_db_loaded()

    if timer_id < 0:
        return {
            "error": (
                "index is required for action='cancel' (use numeric index from list)"
            )
        }

    async with _timer_lock:
        info = _active_timers.pop(timer_id, None)
        if info is not None and not info["task"].done():
            _cancelled_ids.add(timer_id)

    if info is None:
        return {"error": f"Timer with index {timer_id} not found"}

    task = info["task"]
    if not task.done():
        task.cancel()

    async with get_session() as session:
        await delete_timer(session, timer_id)
        await session.commit()

    return {
        "ok": True,
        "cancelled": True,
        "label": info["label"],
        "timer_id": timer_id,
    }


# ══════════════════════════════════════════════════════════════════════════
# Timer task
# ══════════════════════════════════════════════════════════════════════════


async def _timer_task(tid: int, duration_sec: int, label: str) -> None:
    """Sleep for *duration_sec*, then fire the timer."""
    try:
        await asyncio.sleep(duration_sec)
    except asyncio.CancelledError:
        # Timer was cancelled — clean up
        async with _timer_lock:
            _active_timers.pop(tid, None)
            _cancelled_ids.discard(tid)
        return

    # Timer fired — remove from active list and check cancellation race.
    # Wrapped in try/except CancelledError because _timer_cancel() may have
    # called task.cancel() between asyncio.sleep completion and this lock
    # acquisition. If CancelledError propagates out uncaught here,
    # _cancelled_ids.discard() never runs — leaking a stale entry.
    try:
        async with _timer_lock:
            _active_timers.pop(tid, None)
            was_cancelled = tid in _cancelled_ids
            _cancelled_ids.discard(tid)
    except asyncio.CancelledError:
        async with _timer_lock:
            _active_timers.pop(tid, None)
            _cancelled_ids.discard(tid)
        return

    if was_cancelled:
        return

    # Remove from persistent store via ORM
    try:
        async with get_session() as session:
            await delete_timer(session, tid)
            await session.commit()
    except Exception:
        logger.warning("Failed to delete fired timer %d from DB", tid, exc_info=True)

    try:
        from src.core.scheduling.notification_queue import notification_queue

        await notification_queue.enqueue(
            topic="timer",
            text=f"⏰ {label}",
            priority=Notification.PRIORITY_HIGH,
            category="timer",
        )
    except Exception:
        logger.exception("Failed to enqueue timer notification for %r", label)


async def cancel_all_timers() -> None:
    """Cancel all active timers and clear in-memory state.

    Intended for graceful shutdown. Pending timers remain in the DB and will
    be restored on the next startup.
    """
    async with _timer_lock:
        # Populate _cancelled_ids so that any timer already past sleep (fired
        # path) sees itself as cancelled and returns early instead of firing
        # during shutdown (DB access, notification enqueue).
        for tid in _active_timers:
            _cancelled_ids.add(tid)
        tasks = [info["task"] for info in list(_active_timers.values())]
        _active_timers.clear()

    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        # Await so CancelledError propagates and handlers clean up *before*
        # the caller proceeds (e.g. before engine.dispose() in shutdown).
        await asyncio.gather(*tasks, return_exceptions=True)
