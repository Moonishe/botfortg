"""Background task manager with health monitoring and auto-restart."""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    RUNNING = "running"
    FAILED = "failed"
    STOPPED = "stopped"


class RegisteredTask:
    """Metadata for a single registered background task."""

    def __init__(
        self,
        name: str,
        factory: Callable[[], Coroutine[Any, Any, None]],
        *,
        restart_on_failure: bool = True,
        restart_delay: float = 5.0,
        max_restarts: int = 10,
        backoff_base: float = 5.0,
        backoff_max: float = 300.0,
    ) -> None:
        self.name = name
        self.factory = factory
        self.restart_on_failure = restart_on_failure
        self.restart_delay = restart_delay
        self.max_restarts = max_restarts
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.task: asyncio.Task[None] | None = None
        self.status = TaskStatus.STOPPED
        self.restart_count = 0


class BackgroundTaskManager:
    """Manages background asyncio tasks with health monitoring and auto-restart.

    Supports exponential backoff on repeated failures and a configurable
    max restart limit (default 10) with escalation when exceeded.

    Usage::

        manager = BackgroundTaskManager()
        manager.register(
            "my-loop", my_loop,
            restart_on_failure=True, restart_delay=5.0,
            max_restarts=10, backoff_base=5.0, backoff_max=300.0,
        )
        manager.register("my-other", lambda: other_loop(arg))
        manager.start_all()
        # ... later ...
        await manager.stop_all(timeout=30.0)

    Call ``get_status(name)`` or ``get_all_statuses()`` for health checks.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, RegisteredTask] = {}

    def register(
        self,
        name: str,
        factory: Callable[[], Coroutine[Any, Any, None]],
        *,
        restart_on_failure: bool = True,
        restart_delay: float = 5.0,
        max_restarts: int = 10,
        backoff_base: float = 5.0,
        backoff_max: float = 300.0,
    ) -> None:
        """Register a background task.

        Args:
            name: Unique task name (used for the asyncio task name).
            factory: A zero-argument callable that returns a coroutine.
                Use ``lambda`` or ``functools.partial`` to pass arguments.
            restart_on_failure: If True, restart the task on unhandled exception.
            restart_delay: Seconds to wait before restarting (used only for
                the first failure; subsequent failures use exponential backoff).
            max_restarts: Maximum number of restarts before giving up.
            backoff_base: Base delay in seconds for exponential backoff.
            backoff_max: Maximum delay cap in seconds (default 5 minutes).
        """
        if name in self._tasks:
            raise ValueError(f"Task '{name}' is already registered")
        self._tasks[name] = RegisteredTask(
            name,
            factory,
            restart_on_failure=restart_on_failure,
            restart_delay=restart_delay,
            max_restarts=max_restarts,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
        )

    def start_all(self) -> None:
        """Launch all registered tasks."""
        for task in self._tasks.values():
            self._start_single(task)

    def _start_single(self, task: RegisteredTask) -> None:
        """Create and start an asyncio Task that wraps *task.factory*.

        Implements exponential backoff on repeated failures: delay is
        ``backoff_base * 2^(consecutive-1)`` capped at ``backoff_max``.
        After ``max_restarts`` restarts the task is abandoned with a
        critical log (escalation).
        """

        async def wrapper() -> None:
            consecutive = 0
            while True:
                try:
                    task.status = TaskStatus.RUNNING
                    await task.factory()
                except asyncio.CancelledError:
                    task.status = TaskStatus.STOPPED
                    logger.info("Background task '%s' cancelled", task.name)
                    break
                except Exception:  # noqa: BLE001
                    task.status = TaskStatus.FAILED
                    task.restart_count += 1
                    consecutive += 1

                    delay = min(
                        task.backoff_base * (2 ** (consecutive - 1)),
                        task.backoff_max,
                    )

                    logger.exception(
                        "Background task '%s' failed (restart #%d/%d, delay=%.1fs, consecutive=%d)",
                        task.name,
                        task.restart_count,
                        task.max_restarts,
                        delay,
                        consecutive,
                    )

                    if not task.restart_on_failure:
                        logger.info(
                            "Background task '%s' will NOT be restarted",
                            task.name,
                        )
                        break

                    if task.restart_count >= task.max_restarts:
                        logger.critical(
                            "Background task '%s' exceeded max restarts (%d). "
                            "Task will NOT be restarted. ESCALATION REQUIRED.",
                            task.name,
                            task.max_restarts,
                        )
                        task.status = TaskStatus.FAILED
                        try:
                            from src.core.scheduling.notification_queue import (
                                notification_queue,
                            )
                        except ImportError:
                            logger.critical(
                                "Cannot import notification_queue — "
                                "task '%s' escalation notification NOT sent. "
                                "Check module dependencies / circular imports.",
                                task.name,
                            )
                        else:
                            if not hasattr(notification_queue, "enqueue"):
                                logger.critical(
                                    "notification_queue object %r has no 'enqueue' method — "
                                    "task '%s' escalation notification NOT sent. "
                                    "Singleton may not be initialized.",
                                    notification_queue,
                                    task.name,
                                )
                            else:
                                try:
                                    await notification_queue.enqueue(
                                        topic="task_manager",
                                        text=(
                                            f"⛔ Background task <b>«{task.name}»</b> превысила "
                                            f"лимит перезапусков ({task.max_restarts}) и остановлена."
                                        ),
                                        priority=1,
                                    )
                                except Exception:
                                    logger.critical(
                                        "Failed to enqueue task_manager notification for "
                                        "task '%s' — escalation signal lost. "
                                        "Manual investigation required.",
                                        task.name,
                                        exc_info=True,
                                    )
                        break

                    await asyncio.sleep(delay)
                else:
                    # Clean completion — reset consecutive counter
                    task.status = TaskStatus.STOPPED
                    logger.info("Background task '%s' finished cleanly", task.name)
                    break

        task.task = asyncio.create_task(wrapper(), name=task.name)

    async def stop_all(self, *, timeout: float = 30.0) -> None:
        """Cancel all running tasks and wait for completion (with timeout)."""
        for t in self._tasks.values():
            if t.task is not None and not t.task.done():
                t.task.cancel()

        gather = asyncio.gather(
            *(
                t.task
                for t in self._tasks.values()
                if t.task is not None and not t.task.done()
            ),
            return_exceptions=True,
        )
        try:
            await asyncio.wait_for(gather, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Timed out waiting for %d background tasks to stop after %.1fs",
                sum(
                    1
                    for t in self._tasks.values()
                    if t.task is not None and not t.task.done()
                ),
                timeout,
            )

        for t in self._tasks.values():
            t.status = TaskStatus.STOPPED

    def get_status(self, name: str) -> TaskStatus | None:
        """Return the current status of a task, or None if not found."""
        t = self._tasks.get(name)
        if t is None:
            return None
        # Edge-case: task finished outside our wrapper (should not happen).
        if t.task is not None and t.task.done() and t.status is TaskStatus.RUNNING:
            t.status = TaskStatus.FAILED
        return t.status

    def get_all_statuses(self) -> dict[str, TaskStatus]:
        """Return a map of all task names → current status."""
        return {name: self._tasks[name].status for name in self._tasks}

    def task(
        self,
        name: str | None = None,
        *,
        restart_on_failure: bool = True,
        restart_delay: float = 5.0,
    ):
        """Декоратор: регистрирует async-фабрику как background-задачу.

        Использование:
            @task_manager.task("my-task", restart_on_failure=True)
            async def my_task():
                while True:
                    await do_work()
                    await asyncio.sleep(60)
        """

        def decorator(factory):
            task_name = name or factory.__name__
            self.register(
                task_name,
                factory,
                restart_on_failure=restart_on_failure,
                restart_delay=restart_delay,
            )
            return factory

        return decorator


task_manager = BackgroundTaskManager()

# ── Fire-and-forget task tracking ──────────────────────────────
# Tasks created with track_ff() are registered for cleanup during shutdown.
# This prevents data loss when the bot shuts down while background writes
# (trajectory recording, fact saving, inbox processing) are in-flight.

_ff_tasks: set[asyncio.Task] = set()


def track_ff(task: asyncio.Task) -> asyncio.Task:
    """Register a fire-and-forget task for graceful shutdown.

    The task is automatically removed from the tracking set when it completes.
    During shutdown, ``stop_ff_tasks()`` cancels and awaits all tracked tasks.

    Usage::

        track_ff(asyncio.create_task(some_coroutine()))
    """
    # Если задача уже завершена — не добавляем в _ff_tasks:
    # add_done_callback вызовется немедленно, но discard на пустом
    # множестве — no-op, и задача осталась бы висеть в tracking set.
    if task.done():
        return task
    _ff_tasks.add(task)
    task.add_done_callback(_ff_tasks.discard)
    return task


async def stop_ff_tasks(*, timeout: float = 10.0) -> None:
    """Cancel all tracked fire-and-forget tasks and await completion.

    Args:
        timeout: Maximum seconds to wait for all tasks to finish.
    """
    tasks = list(_ff_tasks)
    if not tasks:
        return
    logger.info("Stopping %d fire-and-forget tasks…", len(tasks))
    for t in tasks:
        if not t.done():
            t.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Timed out waiting for %d fire-and-forget tasks after %.1fs",
            sum(1 for t in tasks if not t.done()),
            timeout,
        )
