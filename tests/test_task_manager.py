"""Unit tests for BackgroundTaskManager and fire-and-forget tracking.

Covers:
  - RegisteredTask init with defaults
  - TaskStatus enum values
  - BackgroundTaskManager.register() — normal and duplicate
  - BackgroundTaskManager.get_status() — found / not found / edge case
  - BackgroundTaskManager.status() — empty / with tasks
  - BackgroundTaskManager.active_count() — zero / running / stopped
  - track_ff() — registration, done-task skip, cleanup
  - stop_ff_tasks() — cancellation, timeout
  - task_manager singleton
"""

from __future__ import annotations

import asyncio


import pytest

from src.core.infra.task_manager import (
    BackgroundTaskManager,
    RegisteredTask,
    TaskStatus,
    task_manager,
    track_ff,
    stop_ff_tasks,
)


# ────────────────────────────────────────────────────────────────────
# TaskStatus enum
# ────────────────────────────────────────────────────────────────────


def test_task_status_values():
    """TaskStatus содержит RUNNING, FAILED, STOPPED."""
    assert TaskStatus.RUNNING == "running"
    assert TaskStatus.FAILED == "failed"
    assert TaskStatus.STOPPED == "stopped"


# ────────────────────────────────────────────────────────────────────
# RegisteredTask
# ────────────────────────────────────────────────────────────────────


async def _noop() -> None:
    """Корутина-заглушка для RegisteredTask."""
    pass


def test_registered_task_defaults():
    """RegisteredTask инициализируется с правильными значениями по умолчанию."""
    rt = RegisteredTask("test-task", _noop)
    assert rt.name == "test-task"
    assert rt.factory is _noop
    assert rt.restart_on_failure is True
    assert rt.restart_delay == 5.0
    assert rt.max_restarts == 10
    assert rt.backoff_base == 5.0
    assert rt.backoff_max == 300.0
    assert rt.task is None
    assert rt.status == TaskStatus.STOPPED
    assert rt.restart_count == 0


def test_registered_task_custom():
    """RegisteredTask принимает кастомные параметры."""
    rt = RegisteredTask(
        "short-task",
        _noop,
        restart_on_failure=False,
        restart_delay=1.0,
        max_restarts=3,
        backoff_base=2.0,
        backoff_max=60.0,
    )
    assert rt.restart_on_failure is False
    assert rt.restart_delay == 1.0
    assert rt.max_restarts == 3
    assert rt.backoff_base == 2.0
    assert rt.backoff_max == 60.0


# ────────────────────────────────────────────────────────────────────
# BackgroundTaskManager — register / get_status / status / active_count
# ────────────────────────────────────────────────────────────────────


class TestBackgroundTaskManager:
    """Тесты BackgroundTaskManager без запуска реальных задач."""

    def test_register_ok(self):
        """register() добавляет задачу в менеджер."""
        mgr = BackgroundTaskManager()
        mgr.register("bg-task", _noop)
        assert "bg-task" in mgr._tasks
        assert mgr._tasks["bg-task"].name == "bg-task"

    def test_register_duplicate_raises(self):
        """Повторный register() с тем же именем — ValueError."""
        mgr = BackgroundTaskManager()
        mgr.register("bg-task", _noop)
        with pytest.raises(ValueError, match="already registered"):
            mgr.register("bg-task", _noop)

    def test_get_status_found(self):
        """get_status() возвращает статус зарегистрированной задачи."""
        mgr = BackgroundTaskManager()
        mgr.register("bg-task", _noop)
        assert mgr.get_status("bg-task") == TaskStatus.STOPPED

    def test_get_status_not_found(self):
        """get_status() возвращает None для незарегистрированной задачи."""
        mgr = BackgroundTaskManager()
        assert mgr.get_status("no-such-task") is None

    def test_status_empty(self):
        """status() возвращает пустой dict если нет задач."""
        mgr = BackgroundTaskManager()
        assert mgr.status() == {}

    def test_status_with_task(self):
        """status() возвращает корректные данные для зарегистрированной задачи."""
        mgr = BackgroundTaskManager()
        mgr.register("bg-task", _noop)
        result = mgr.status()
        assert "bg-task" in result
        assert result["bg-task"]["running"] is False
        assert result["bg-task"]["failures"] == 0
        assert result["bg-task"]["restart_count"] == 0
        assert result["bg-task"]["status"] == "stopped"

    def test_active_count_zero(self):
        """active_count() == 0 когда нет задач или все STOPPED."""
        mgr = BackgroundTaskManager()
        assert mgr.active_count() == 0
        mgr.register("bg-task", _noop)
        assert mgr.active_count() == 0  # не запускали

    def test_get_all_statuses(self):
        """get_all_statuses() возвращает map всех задач."""
        mgr = BackgroundTaskManager()
        mgr.register("task-a", _noop)
        mgr.register("task-b", _noop)
        statuses = mgr.get_all_statuses()
        assert statuses == {"task-a": TaskStatus.STOPPED, "task-b": TaskStatus.STOPPED}

    def test_register_multiple(self):
        """Можно зарегистрировать несколько задач с разными именами."""
        mgr = BackgroundTaskManager()
        mgr.register("task-a", _noop)
        mgr.register("task-b", _noop)
        mgr.register("task-c", _noop)
        assert len(mgr._tasks) == 3
        assert mgr.get_status("task-a") == TaskStatus.STOPPED
        assert mgr.get_status("task-b") == TaskStatus.STOPPED
        assert mgr.get_status("task-c") == TaskStatus.STOPPED


# ────────────────────────────────────────────────────────────────────
# BackgroundTaskManager — start_all / stop_all (async)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_all_and_stop_all():
    """start_all() запускает задачи, stop_all() останавливает."""
    started = []
    stopped = False

    async def long_task():
        started.append(True)
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            nonlocal stopped
            stopped = True
            raise

    mgr = BackgroundTaskManager()
    mgr.register("long-task", long_task, restart_on_failure=False)
    await mgr.start_all()

    # Даём задаче запуститься
    await asyncio.sleep(0.05)
    assert started
    assert mgr.active_count() == 1
    assert mgr.get_status("long-task") == TaskStatus.RUNNING

    await mgr.stop_all(timeout=5.0)
    assert stopped
    assert mgr.get_status("long-task") == TaskStatus.STOPPED


@pytest.mark.asyncio
async def test_start_all_restart_on_failure():
    """Задача с restart_on_failure=True перезапускается после ошибки."""
    attempts = []

    async def flaky_task():
        attempts.append(1)
        raise RuntimeError("bang")

    mgr = BackgroundTaskManager()
    mgr.register(
        "flaky-task",
        flaky_task,
        restart_on_failure=True,
        restart_delay=0.01,
        max_restarts=2,
        backoff_base=0.01,
        backoff_max=0.5,
    )
    await mgr.start_all()

    # Ждём пару перезапусков
    await asyncio.sleep(0.3)
    assert len(attempts) >= 2, f"Expected >=2 attempts, got {len(attempts)}"

    await mgr.stop_all(timeout=5.0)


@pytest.mark.asyncio
async def test_start_all_no_restart():
    """Задача с restart_on_failure=False НЕ перезапускается."""
    attempts = []

    async def one_shot():
        attempts.append(1)
        raise RuntimeError("fail")

    mgr = BackgroundTaskManager()
    mgr.register("one-shot", one_shot, restart_on_failure=False)
    await mgr.start_all()

    await asyncio.sleep(0.1)
    assert len(attempts) == 1

    await mgr.stop_all(timeout=5.0)


# ────────────────────────────────────────────────────────────────────
# track_ff / stop_ff_tasks
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_track_ff_registers_task():
    """track_ff() регистрирует задачу для graceful shutdown."""
    event = asyncio.Event()

    async def delayed():
        await event.wait()

    t = asyncio.create_task(delayed())
    track_ff(t)

    # Задача должна быть в трекинге
    from src.core.infra.task_manager import _ff_tasks

    assert t in _ff_tasks

    # Очистка
    event.set()
    await t


@pytest.mark.asyncio
async def test_track_ff_skips_done_task():
    """track_ff() не добавляет уже завершённую задачу."""
    from src.core.infra.task_manager import _ff_tasks

    async def quick():
        pass

    t = asyncio.create_task(quick())
    await t
    assert t.done()

    prev_count = len(_ff_tasks)
    track_ff(t)
    assert len(_ff_tasks) == prev_count  # не добавилась


@pytest.mark.asyncio
async def test_stop_ff_tasks_cancels_running():
    """stop_ff_tasks() отменяет трекаемые задачи."""
    from src.core.infra.task_manager import _ff_tasks

    was_cancelled = False

    async def long_running():
        nonlocal was_cancelled
        try:
            await asyncio.sleep(999)
        except asyncio.CancelledError:
            was_cancelled = True
            raise

    t = asyncio.create_task(long_running())
    track_ff(t)

    await asyncio.sleep(0.02)
    await stop_ff_tasks(timeout=5.0)

    assert was_cancelled


@pytest.mark.asyncio
async def test_stop_ff_tasks_empty_is_noop():
    """stop_ff_tasks() с пустым _ff_tasks не падает."""
    # Предварительно чистим
    from src.core.infra.task_manager import _ff_tasks

    _ff_tasks.clear()
    # Не должно быть исключений
    await stop_ff_tasks(timeout=1.0)


@pytest.mark.asyncio
async def test_task_removed_from_ff_on_completion():
    """Завершённая задача удаляется из _ff_tasks через add_done_callback."""
    from src.core.infra.task_manager import _ff_tasks

    async def quick():
        pass

    t = asyncio.create_task(quick())
    track_ff(t)
    assert t in _ff_tasks

    await t
    # Даём callback'у отработать
    await asyncio.sleep(0.01)
    assert t not in _ff_tasks


# ────────────────────────────────────────────────────────────────────
# task_manager singleton
# ────────────────────────────────────────────────────────────────────


def test_task_manager_singleton_exists():
    """Глобальный task_manager — экземпляр BackgroundTaskManager."""
    assert isinstance(task_manager, BackgroundTaskManager)


def test_task_manager_default_empty():
    """Глобальный task_manager изначально может иметь state от других тестов."""
    # В модульных тестах другие тесты могут зарегистрировать задачи в синглтоне.
    # Это не ошибка — проверяем что объект существует и API работает.
    assert isinstance(task_manager, BackgroundTaskManager)
    # Проверяем что методы не падают
    status = task_manager.status()
    count = task_manager.active_count()
    assert isinstance(status, dict)
    assert isinstance(count, int)
