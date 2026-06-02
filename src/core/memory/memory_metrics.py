"""Memory Metrics Collector — time-series отслеживание здоровья памяти (Phase 2).

Собирает метрики в рантайме: health_score, recall_hit_rate, contradiction_count,
supersedes_count, pre_filter_reject_rate, extraction_latency.

Использование:
    from src.core.memory.memory_metrics import memory_metrics

    memory_metrics.record_health(score=85, components={...})
    memory_metrics.record_contradiction()
    await memory_metrics.snapshot()  # -> {health_score: ..., trends: ...}

Все методы потокобезопасны (asyncio.Lock). Хранит последние N точек (rolling window).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.core.memory.ttl_cache import TTLCache

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MetricPoint:
    """Одна точка метрики с timestamp."""

    timestamp: float
    value: float
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryMetricsSnapshot:
    """Сводка метрик памяти на текущий момент."""

    health_score: float = 0.0
    health_trend: str = "stable"  # up / down / stable
    health_components: dict = field(default_factory=dict)

    total_facts: int = 0
    active_facts: int = 0
    inactive_facts: int = 0

    contradictions_total: int = 0
    supersedes_chains: int = 0
    supersedes_avg_chain_length: float = 0.0

    pre_filter_accepts: int = 0
    pre_filter_rejects: int = 0
    pre_filter_reject_rate: float = 0.0

    extractions_total: int = 0
    extraction_avg_latency_ms: float = 0.0

    recall_hit_rate: float = 0.0
    recall_cache_hit_rate: float = 0.0

    generated_at: str = ""


class MemoryMetricsCollector:
    """Синглтон-коллектор метрик памяти.

    Хранит rolling window из последних N точек для каждой метрики.
    Сводка кэшируется в TTLCache на 60s.
    """

    MAX_POINTS = 500  # rolling window на метрику

    def __init__(self):
        self._lock = asyncio.Lock()
        # Time-series
        self._health: list[MetricPoint] = []
        self._contradictions: list[MetricPoint] = []
        self._supersedes: list[MetricPoint] = []
        self._extractions: list[MetricPoint] = []
        self._extraction_latencies: list[float] = []  # ms
        self._pre_filter_accepts: int = 0
        self._pre_filter_rejects: int = 0
        # Recall cache stats (external, set via set_cache_stats)
        self._recall_hits: int = 0
        self._recall_misses: int = 0
        self._recall_cache_hits: int = 0
        self._recall_cache_misses: int = 0
        # Facts count (external, set via set_fact_counts)
        self._total_facts: int = 0
        self._active_facts: int = 0
        self._inactive_facts: int = 0
        # Supersedes chain lengths
        self._supersedes_chain_lengths: list[int] = []
        # Snapshot cache
        self._snapshot_cache = TTLCache[str, MemoryMetricsSnapshot](
            max_size=2, default_ttl=60.0, name="metrics_snapshot"
        )

    # ---- Record API ----

    async def record_health(self, score: float, components: dict | None = None) -> None:
        """Записать балл здоровья."""
        async with self._lock:
            self._health.append(
                MetricPoint(
                    timestamp=time.time(),
                    value=score,
                    tags={"components": str(components or {})},
                )
            )
            if len(self._health) > self.MAX_POINTS:
                self._health = self._health[-self.MAX_POINTS :]

    async def record_contradiction(self) -> None:
        """Записать обнаруженное противоречие."""
        async with self._lock:
            self._contradictions.append(MetricPoint(timestamp=time.time(), value=1.0))
            if len(self._contradictions) > self.MAX_POINTS:
                self._contradictions = self._contradictions[-self.MAX_POINTS :]

    async def record_supersedes_chain(self, chain_length: int) -> None:
        """Записать созданную supersedes-цепочку."""
        async with self._lock:
            self._supersedes.append(
                MetricPoint(timestamp=time.time(), value=float(chain_length))
            )
            self._supersedes_chain_lengths.append(chain_length)
            if len(self._supersedes) > self.MAX_POINTS:
                self._supersedes = self._supersedes[-self.MAX_POINTS :]
            if len(self._supersedes_chain_lengths) > self.MAX_POINTS:
                self._supersedes_chain_lengths = self._supersedes_chain_lengths[
                    -self.MAX_POINTS :
                ]

    async def record_extraction(self, latency_ms: float) -> None:
        """Записать извлечение с latency."""
        async with self._lock:
            self._extractions.append(
                MetricPoint(timestamp=time.time(), value=latency_ms)
            )
            self._extraction_latencies.append(latency_ms)
            if len(self._extractions) > self.MAX_POINTS:
                self._extractions = self._extractions[-self.MAX_POINTS :]
            if len(self._extraction_latencies) > self.MAX_POINTS:
                self._extraction_latencies = self._extraction_latencies[
                    -self.MAX_POINTS :
                ]

    async def record_pre_filter(self, accepted: bool) -> None:
        """Записать решение pre_filter."""
        async with self._lock:
            if accepted:
                self._pre_filter_accepts += 1
            else:
                self._pre_filter_rejects += 1

    async def record_recall(self, hit: bool) -> None:
        """Записать результат recall (hit/miss)."""
        async with self._lock:
            if hit:
                self._recall_hits += 1
            else:
                self._recall_misses += 1

    async def record_recall_cache(self, hit: bool) -> None:
        """Записать cache hit/miss для recall."""
        async with self._lock:
            if hit:
                self._recall_cache_hits += 1
            else:
                self._recall_cache_misses += 1

    async def set_fact_counts(self, total: int, active: int, inactive: int) -> None:
        """Обновить счётчики фактов."""
        async with self._lock:
            self._total_facts = total
            self._active_facts = active
            self._inactive_facts = inactive

    # ---- Snapshot API ----

    async def snapshot(self, force: bool = False) -> MemoryMetricsSnapshot:
        """Собрать сводку метрик (кэшируется 60s).

        force=True — игнорировать кэш.
        """
        cache_key = "snap"
        if not force:
            cached = await self._snapshot_cache.get(cache_key)
            if cached is not None:
                return cached

        snap = await self._build_snapshot()
        await self._snapshot_cache.set(cache_key, snap, ttl=60.0)
        return snap

    async def _build_snapshot(self) -> MemoryMetricsSnapshot:
        """Строит сводку (вызывается под локом)."""
        async with self._lock:
            snap = MemoryMetricsSnapshot()

            # Health
            if self._health:
                snap.health_score = self._health[-1].value
                snap.health_trend = self._compute_trend([p.value for p in self._health])
            if self._health:
                # Try to parse components from last point tags
                try:
                    import json

                    snap.health_components = json.loads(
                        self._health[-1].tags.get("components", "{}").replace("'", '"')
                    )
                except Exception:
                    snap.health_components = {}

            # Facts
            snap.total_facts = self._total_facts
            snap.active_facts = self._active_facts
            snap.inactive_facts = self._inactive_facts

            # Contradictions
            snap.contradictions_total = len(self._contradictions)

            # Supersedes
            snap.supersedes_chains = len(self._supersedes)
            if self._supersedes_chain_lengths:
                snap.supersedes_avg_chain_length = sum(
                    self._supersedes_chain_lengths
                ) / len(self._supersedes_chain_lengths)

            # Pre-filter
            pf_total = self._pre_filter_accepts + self._pre_filter_rejects
            snap.pre_filter_accepts = self._pre_filter_accepts
            snap.pre_filter_rejects = self._pre_filter_rejects
            snap.pre_filter_reject_rate = (
                self._pre_filter_rejects / pf_total if pf_total > 0 else 0.0
            )

            # Extractions
            snap.extractions_total = len(self._extractions)
            if self._extraction_latencies:
                snap.extraction_avg_latency_ms = sum(self._extraction_latencies) / len(
                    self._extraction_latencies
                )

            # Recall
            r_total = self._recall_hits + self._recall_misses
            snap.recall_hit_rate = self._recall_hits / r_total if r_total > 0 else 0.0
            rc_total = self._recall_cache_hits + self._recall_cache_misses
            snap.recall_cache_hit_rate = (
                self._recall_cache_hits / rc_total if rc_total > 0 else 0.0
            )

            snap.generated_at = datetime.now(timezone.utc).isoformat()

            return snap

    async def reset(self) -> None:
        """Сбросить ВСЕ метрики (для тестов)."""
        async with self._lock:
            self._health.clear()
            self._contradictions.clear()
            self._supersedes.clear()
            self._extractions.clear()
            self._extraction_latencies.clear()
            self._supersedes_chain_lengths.clear()
            self._pre_filter_accepts = 0
            self._pre_filter_rejects = 0
            self._recall_hits = 0
            self._recall_misses = 0
            self._recall_cache_hits = 0
            self._recall_cache_misses = 0
            self._total_facts = 0
            self._active_facts = 0
            self._inactive_facts = 0
            await self._snapshot_cache.clear()

    # ---- Internal ----

    @staticmethod
    def _compute_trend(values: list[float], window: int = 10) -> str:
        """Определяет тренд: 'up', 'down', 'stable'."""
        if len(values) < 2:
            return "stable"
        recent = values[-min(window, len(values)) :]
        if len(recent) < 2:
            return "stable"
        # Линейная регрессия наклон
        n = len(recent)
        x_mean = (n - 1) / 2
        y_mean = sum(recent) / n
        numerator = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator == 0:
            return "stable"
        slope = numerator / denominator
        # Порог 1% от y_mean
        threshold = abs(y_mean) * 0.01 if y_mean != 0 else 0.1
        if slope > threshold:
            return "up"
        elif slope < -threshold:
            return "down"
        return "stable"


# Синглтон
memory_metrics = MemoryMetricsCollector()
