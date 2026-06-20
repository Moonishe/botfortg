import asyncio
import importlib
from datetime import datetime
from datetime import UTC as datetime_utc
from unittest.mock import patch

import pytest

import src.core.actions.mcp_timer as mcp_timer
from src.core.actions.mcp_timer import (
    _timer_alarm,
    _timer_cancel,
    _timer_list,
    _timer_set,
)
from src.db.repo import list_pending_timers
from src.db.session import get_session


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 31, 23, 59, tzinfo=datetime_utc)


async def _cancel_and_await() -> None:
    """Cancel all active timer tasks and await their cleanup."""
    async with mcp_timer._timer_lock:
        for tid in mcp_timer._active_timers:
            mcp_timer._cancelled_ids.add(tid)
        tasks = [info["task"] for info in list(mcp_timer._active_timers.values())]
        mcp_timer._active_timers.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    mcp_timer._cancelled_ids.clear()


@pytest.fixture(autouse=True)
async def _reset_timer_state():
    """Reset module-level timer state before and after each test.

    Awaits cancelled tasks so their CancelledError handlers finish before
    the next test starts — prevents stale task handlers from interfering
    with the reset module state (e.g. tid collision).
    """
    await _cancel_and_await()
    mcp_timer._timer_counter = 0
    mcp_timer._db_loaded = False
    yield
    await _cancel_and_await()


@pytest.mark.usefixtures("_db_init")
@pytest.mark.asyncio
async def test_alarm_after_passed_time_rolls_over_month_end() -> None:
    with patch("src.core.actions.mcp_timer.datetime", FixedDateTime):
        result = await _timer_alarm("23:58", "month-end")

    try:
        assert result["ok"] is True
        assert result["will_fire_at"].startswith("2026-02-01T23:58:00")
    finally:
        await _timer_cancel(result["timer_id"])


@pytest.mark.usefixtures("_db_init")
@pytest.mark.asyncio
async def test_set_lists_and_cancels() -> None:
    """set -> list -> cancel works and removes the timer."""
    result = await _timer_set(3600, "test set")
    assert result["ok"] is True
    tid = result["timer_id"]

    listed = await _timer_list()
    assert listed["ok"] is True
    assert any(t["index"] == tid for t in listed["timers"])

    cancel_result = await _timer_cancel(tid)
    assert cancel_result["ok"] is True

    listed_after = await _timer_list()
    assert not any(t["index"] == tid for t in listed_after["timers"])


@pytest.mark.usefixtures("_db_init")
@pytest.mark.asyncio
async def test_invalid_duration_returns_error() -> None:
    assert (await _timer_set(0, "zero"))[
        "error"
    ] == "duration_sec must be a positive integer"
    assert (await _timer_set(-5, "negative"))[
        "error"
    ] == "duration_sec must be a positive integer"


@pytest.mark.usefixtures("_db_init")
@pytest.mark.asyncio
async def test_invalid_alarm_time_returns_error() -> None:
    assert (await _timer_alarm("", "empty"))["error"].startswith("time must be")
    assert (await _timer_alarm("25:00", "bad hour"))["error"].startswith("time must be")
    assert (await _timer_alarm("12:60", "bad minute"))["error"].startswith(
        "time must be"
    )
    assert (await _timer_alarm("12:30:45", "extra seconds"))["error"].startswith(
        "time must be"
    )


@pytest.mark.usefixtures("_db_init")
@pytest.mark.asyncio
async def test_cancel_nonexistent_returns_error() -> None:
    assert (await _timer_cancel(9999))["error"].startswith("Timer with index")


@pytest.mark.usefixtures("_db_init")
@pytest.mark.asyncio
async def test_restore_pending_timers_after_restart() -> None:
    """Pending timers in the DB are reloaded into _active_timers on load."""
    result = await _timer_set(600, "restore me")
    assert result["ok"] is True
    tid = result["timer_id"]

    # Verify the DB row was committed before simulating a restart.
    async with get_session() as session:
        rows = await list_pending_timers(session)
    assert any(t.timer_id == tid for t in rows), (
        f"Timer {tid} not persisted to DB before restart simulation"
    )

    # Simulate process restart: drop in-memory state but keep DB rows.
    for info in list(mcp_timer._active_timers.values()):
        info["task"].cancel()
    mcp_timer._active_timers.clear()
    mcp_timer._cancelled_ids.clear()
    mcp_timer._timer_counter = 0
    mcp_timer._db_loaded = False

    await mcp_timer._ensure_timer_db_loaded()

    listed = await _timer_list()
    restored = [t for t in listed["timers"] if t["index"] == tid]
    assert len(restored) == 1
    assert restored[0]["label"] == "restore me"

    # Cleanup the restored task so it does not fire during the test run.
    await _timer_cancel(tid)


def test_import_does_not_access_database() -> None:
    """Importing mcp_timer should be side-effect-free (no DB access on import).

    With SQLAlchemy ORM, no database operations happen at import time.
    The _ensure_timer_db_loaded() is only called lazily on first use.
    """
    importlib.reload(mcp_timer)
    # If we got here without errors, the import is side-effect-free.
    assert mcp_timer._db_loaded is False, "import should not trigger DB load"
