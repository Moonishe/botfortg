"""Kanban Board — task coordination for sub-agents.

JSON-file persistence (no ORM changes). Integrates with AgentOrchestrator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

# ── Task lifecycle constants ──────────────────────────────────────────
_TASK_MAX_AGE_DAYS = 30  # Auto-remove done/failed tasks older than this


@dataclass
class KanbanTask:
    """A single task on the kanban board."""

    id: str
    title: str
    status: str = "todo"  # todo, in_progress, done, failed
    assigned_agent: str | None = None
    priority: int = 2  # 1=low, 2=normal, 3=high
    plan_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    result: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = time.time()


class KanbanBoard:
    """Kanban board with JSON-file persistence.

    Usage::

        board = KanbanBoard(owner_id=123)
        await board.initialize()
        await board.add_task(title="Fix bug", priority=3)
        task = board.get_next_task()  # returns ready task
        await board.update_status(task.id, "done", result="Fixed!")
    """

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id
        self._tasks: dict[str, KanbanTask] = {}
        self._file = DATA_DIR / f"kanban_{owner_id}.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Load board from disk and clean up (call once after creation)."""
        await asyncio.gather(
            asyncio.to_thread(self._cleanup_orphaned_tmp_sync),
            asyncio.to_thread(self._load_sync),
        )
        async with self._lock:
            self._prune_old_tasks_locked()
            await self._save()
        logger.debug(
            "Kanban board initialized for user %d (%d tasks)",
            self.owner_id,
            len(self._tasks),
        )

    # ── Persistence ──────────────────────────────────────────────

    def _file_path(self) -> Path:
        return self._file

    def _cleanup_orphaned_tmp_sync(self) -> None:
        """Remove orphaned .tmp files from failed atomic writes (best-effort, sync — called via asyncio.to_thread)."""
        tmp = self._file_path().with_suffix(".tmp")
        if tmp.exists():
            try:
                tmp.unlink()
                logger.debug("Cleaned up orphaned tmp: %s", tmp)
            except OSError:
                logger.debug("Failed to clean orphaned tmp: %s", tmp, exc_info=True)

    def _prune_old_tasks_locked(self) -> None:
        """Remove done/failed tasks older than _TASK_MAX_AGE_DAYS.

        Prevents unbounded growth of in-memory task dict and JSON persistence.
        Called during initialization (under lock) to keep the working set small.
        """
        cutoff = time.time() - (_TASK_MAX_AGE_DAYS * 86400)
        to_remove: list[str] = [
            tid
            for tid, t in self._tasks.items()
            if t.status in ("done", "failed") and t.updated_at < cutoff
        ]
        if to_remove:
            for tid in to_remove:
                del self._tasks[tid]
            logger.info(
                "Pruned %d old tasks (done/failed >%dd) for user %d",
                len(to_remove),
                _TASK_MAX_AGE_DAYS,
                self.owner_id,
            )

    def _load_sync(self) -> None:
        """Load board from JSON file (sync — called via asyncio.to_thread)."""
        path = self._file_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning(
                    "Kanban board JSON is not a dict (type=%s), ignoring",
                    type(data).__name__,
                )
                return
            tasks_raw = data.get("tasks", [])
            # Guard: if "tasks" key exists but is None/null or not a list
            if not isinstance(tasks_raw, list):
                logger.warning(
                    "Kanban board tasks is not a list (type=%s), skipping load",
                    type(tasks_raw).__name__,
                )
                return
            for task_data in tasks_raw:
                # Filter out keys not present in KanbanTask dataclass
                valid_keys = {
                    "id",
                    "title",
                    "status",
                    "assigned_agent",
                    "priority",
                    "plan_id",
                    "dependencies",
                    "result",
                    "created_at",
                    "updated_at",
                }
                if not isinstance(task_data, dict):
                    logger.warning("Skipping non-dict task entry: %r", task_data)
                    continue
                filtered = {k: v for k, v in task_data.items() if k in valid_keys}
                if (
                    "id" not in filtered
                    or "title" not in filtered
                    or not isinstance(filtered.get("id"), str)
                    or not filtered["id"].strip()
                    or not isinstance(filtered.get("title"), str)
                ):
                    logger.warning("Skipping task with invalid id/title: %r", filtered)
                    continue
                # ── Type coercion: JSON may have wrong types ─────────
                # priority: ensure int (float → truncate, string → parse, else 2)
                try:
                    filtered["priority"] = int(filtered.get("priority", 2))
                except (ValueError, TypeError):
                    filtered["priority"] = 2
                if filtered["priority"] not in (1, 2, 3):
                    filtered["priority"] = 2
                # status: ensure valid
                if filtered.get("status") not in self._VALID_STATUSES:
                    filtered["status"] = "todo"
                # dependencies: ensure list of strings
                deps = filtered.get("dependencies")
                if not isinstance(deps, list) or not all(
                    isinstance(d, str) for d in deps
                ):
                    filtered["dependencies"] = []
                # created_at / updated_at: ensure float
                for ts_field in ("created_at", "updated_at"):
                    try:
                        filtered[ts_field] = float(filtered.get(ts_field, 0.0))
                    except (ValueError, TypeError):
                        filtered[ts_field] = 0.0
                # ── Construct task ────────────────────────────────────
                try:
                    task = KanbanTask(**filtered)
                except TypeError as exc:
                    logger.warning("Skipping malformed task %r: %s", filtered, exc)
                    continue
                self._tasks[task.id] = task
            logger.debug("Loaded %d tasks for user %d", len(self._tasks), self.owner_id)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to load kanban board: %s", exc)

    async def _save(self) -> None:
        """Save board to JSON file atomically.

        IMPORTANT: Caller must hold ``self._lock`` before calling this method.
        """
        data: dict[str, Any] = {
            "owner_id": self.owner_id,
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "assigned_agent": t.assigned_agent,
                    "priority": t.priority,
                    "plan_id": t.plan_id,
                    "dependencies": t.dependencies,
                    "result": t.result,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                }
                for t in self._tasks.values()
            ],
            "updated_at": time.time(),
        }
        path = self._file_path()
        tmp = path.with_suffix(".tmp")

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)

        await asyncio.to_thread(_write)

    # ── CRUD ─────────────────────────────────────────────────────

    async def add_task(
        self,
        title: str,
        priority: int = 2,
        dependencies: list[str] | None = None,
        plan_id: str | None = None,
    ) -> KanbanTask:
        """Add a new task to the board (async, lock-protected)."""
        if priority not in (1, 2, 3):
            logger.warning("Invalid priority %d, defaulting to 2", priority)
            priority = 2
        task = KanbanTask(
            id=secrets.token_hex(6),  # 12 chars, ~2.8×10^14 combinations
            title=(title or "")[:200],  # max 200 chars, None-safe
            priority=priority,
            dependencies=dependencies or [],
            plan_id=plan_id,
        )
        async with self._lock:
            self._tasks[task.id] = task
            await self._save()
        return task

    async def get_next_task(self) -> KanbanTask | None:
        """Get the highest-priority ready task (dependencies satisfied, lock-protected)."""
        async with self._lock:
            ready = [
                t
                for t in self._tasks.values()
                if t.status == "todo"
                and all(
                    self._tasks.get(d) and self._tasks[d].status == "done"
                    for d in t.dependencies
                )
            ]
            if not ready:
                return None
            # Sort by priority (desc), then creation time (asc)
            ready.sort(key=lambda t: (-t.priority, t.created_at))
            return ready[0]

    _VALID_STATUSES = frozenset({"todo", "in_progress", "done", "failed"})

    # Valid state transitions: current → allowed next states
    _VALID_TRANSITIONS: dict[str, frozenset[str]] = {
        "todo": frozenset({"in_progress", "failed"}),
        "in_progress": frozenset({"done", "failed"}),
        "done": frozenset(),
        "failed": frozenset(),
    }

    async def update_status(
        self, task_id: str, status: str, result: str | None = None
    ) -> bool:
        """Update task status and optionally result (async, lock-protected)."""
        if status not in self._VALID_STATUSES:
            logger.warning(
                "Invalid kanban status %r (valid: %s)",
                status,
                sorted(self._VALID_STATUSES),
            )
            return False
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            allowed = self._VALID_TRANSITIONS.get(task.status, frozenset())
            if status not in allowed:
                logger.warning(
                    "Invalid kanban transition %r → %r (allowed: %s)",
                    task.status,
                    status,
                    sorted(allowed),
                )
                return False
            task.status = status
            task.updated_at = time.time()
            if result is not None:
                task.result = str(result)[:2000]  # max 2000 chars, safe for any type
            await self._save()
        return True

    async def assign_agent(self, task_id: str, agent_name: str) -> bool:
        """Assign a task to an agent (async, lock-protected).

        Only transitions from 'todo' → 'in_progress' are allowed;
        prevents race where two agents claim the same task.
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status != "todo":
                logger.warning(
                    "Cannot assign task %r: expected 'todo', got %r",
                    task_id,
                    task.status,
                )
                return False
            task.assigned_agent = agent_name
            task.status = "in_progress"
            task.updated_at = time.time()
            await self._save()
        return True

    async def get_board(self) -> dict[str, Any]:
        """Get full board summary (lock-protected snapshot)."""
        async with self._lock:
            tasks_by_status: dict[str, list[dict[str, Any]]] = {}
            for t in self._tasks.values():
                tasks_by_status.setdefault(t.status, []).append(
                    {
                        "id": t.id,
                        "title": t.title,
                        "priority": t.priority,
                        "assigned_agent": t.assigned_agent,
                    }
                )
            return {
                "owner_id": self.owner_id,
                "total": len(self._tasks),
                "by_status": {s: len(ts) for s, ts in tasks_by_status.items()},
                "tasks": tasks_by_status,
            }

    async def from_plan(self, plan: Any) -> int:
        """Convert an HTN Plan into kanban tasks.

        Args:
            plan: A Plan object from htn_planner (has ``.steps`` attribute).

        Returns:
            Number of tasks created.
        """
        if plan is None or not hasattr(plan, "steps"):
            return 0
        steps = plan.steps
        if (
            steps is None
            or not hasattr(steps, "__iter__")
            or isinstance(steps, (str, bytes))
        ):
            return 0

        plan_id = getattr(plan, "plan_id", None)
        # Build tasks without lock first (idempotent token generation).
        new_tasks: list[KanbanTask] = []
        for step in steps:
            # getattr fallback only triggers when attribute is missing,
            # NOT when it is None. So combine with `or` for None-safety.
            title = (getattr(step, "description", None) or str(step))[:200]
            new_tasks.append(
                KanbanTask(
                    id=secrets.token_hex(6),
                    title=title,
                    priority=2,
                    plan_id=plan_id,
                )
            )

        # Single lock acquisition + single atomic save for all tasks.
        if new_tasks:
            async with self._lock:
                for task in new_tasks:
                    self._tasks[task.id] = task
                await self._save()

        return len(new_tasks)
