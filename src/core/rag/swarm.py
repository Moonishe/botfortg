"""Swarm Orchestrator — параллельный запуск sub-agent'ов с consensus-протоколом."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from src.config import settings
from src.core.rag.types import SwarmSubTask, SwarmResult, ConsensusResult

logger = logging.getLogger(__name__)

# Callback: (query: str) → list[ResearchSource]
SearchCallback = Callable[[str], Awaitable[list[Any]]]


class SwarmOrchestrator:
    """Параллельный запуск sub-agent'ов на каждую подтему + consensus."""

    def __init__(self, max_parallel: int = 3) -> None:
        self._sem = asyncio.Semaphore(max_parallel or settings.swarm_max_parallel)

    async def execute(
        self,
        subtasks: list[SwarmSubTask],
        search_fn: SearchCallback | None = None,
    ) -> tuple[list[SwarmResult], ConsensusResult]:
        """Запустить все подзадачи параллельно с семафором.

        Args:
            subtasks: Список подзадач для параллельного выполнения.
            search_fn: Опциональный коллбек для поиска (query → источники).
                       Если None — swarm только собирает консенсус.

        Returns:
            Кортеж (результаты подзадач, консенсус).
        """
        if not settings.deep_research_swarm_enabled or not subtasks:
            return [], ConsensusResult()

        async def _run_one(task: SwarmSubTask) -> SwarmResult:
            async with self._sem:
                return await self._run_subtopic(task, search_fn)

        # asyncio.gather(return_exceptions=True) never raises Exception;
        # asyncio.CancelledError (BaseException) can still propagate on cancellation,
        # but that is intended — swarm should not suppress task cancellation.
        results = await asyncio.gather(
            *[_run_one(t) for t in subtasks],
            return_exceptions=True,
        )

        clean_results: list[SwarmResult] = []
        for r in results:
            if isinstance(r, SwarmResult):
                clean_results.append(r)
            else:
                logger.warning("Swarm subtask failed: %s", r)

        consensus = await self._reach_consensus(clean_results)
        return clean_results, consensus

    async def _run_subtopic(
        self,
        task: SwarmSubTask,
        search_fn: SearchCallback | None = None,
    ) -> SwarmResult:
        """Выполнить одну подзадачу: поиск по подтеме.

        Если передан search_fn — выполняет реальный поиск и заполняет
        sources + claims. Иначе возвращает заглушку (только консенсус).
        """
        result = SwarmResult(
            subtask_id=f"swarm_{uuid.uuid4().hex[:8]}",
            status="running",
        )

        try:
            if search_fn is not None:
                sources = await search_fn(task.query)
                result.sources = sources
                result.summary = f"Found {len(sources)} sources for: {task.query}"
            else:
                result.summary = f"Searched: {task.query}"

            result.status = "completed"
        except TimeoutError:
            result.status = "timed_out"
            logger.warning("Swarm subtask %s timed out", task.subtopic)
        except Exception:
            result.status = "failed"
            logger.debug("Swarm subtask %s failed", task.subtopic, exc_info=True)

        return result

    @staticmethod
    def merge_sources(results: list[SwarmResult]) -> list[Any]:
        """Объединить и дедуплицировать источники из нескольких результатов swarm'а.

        Дедупликация по URL (или по строковому представлению, если url отсутствует).
        """
        seen_urls: set[str] = set()
        merged: list[Any] = []
        for r in results:
            for src in r.sources:
                url = getattr(src, "url", str(src))
                if url not in seen_urls:
                    seen_urls.add(url)
                    merged.append(src)
        return merged

    async def _reach_consensus(
        self,
        results: list[SwarmResult],
    ) -> ConsensusResult:
        """Простой консенсус: agreed = completed, disputed = failed."""
        consensus = ConsensusResult()
        for r in results:
            if r.status == "completed":
                consensus.agreed_claims.extend(r.claims)
            elif r.status in ("failed", "timed_out"):
                consensus.disputed_claims.extend(r.claims)
            else:
                consensus.orphan_claims.extend(r.claims)
        return consensus
