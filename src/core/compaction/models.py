"""Dataclasses for the Compaction Pipeline v2."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class NudgeCandidate:
    """One memory fact selected for human confirmation."""

    memory_id: int
    fact: str
    confidence: float
    use_count: int
    created_at: str | None = None


@dataclass(slots=True)
class CompressResult:
    """Outcome of the temporal + semantic compression phase."""

    groups_examined: int = 0
    groups_compressed: int = 0
    facts_merged: int = 0
    facts_deactivated: int = 0


@dataclass(slots=True)
class TrajectorySkillCandidate:
    """A skill proposed from a group of successful trajectories."""

    skill_id: int | None = None
    name: str = ""
    description: str | None = None
    confidence: float = 0.0


@dataclass(slots=True)
class CompactionReport:
    """Aggregated result of running all 7 compaction phases."""

    # PRUNE
    facts_pruned: int = 0
    longterm_protected: int = 0

    # NUDGE
    facts_nudged: int = 0
    nudge_confirmed: int = 0
    nudge_forgotten: int = 0
    nudge_edited: int = 0

    # COMPRESS
    groups_examined: int = 0
    groups_compressed: int = 0
    facts_merged: int = 0

    # REVAL (mirrors RevalBatchSummary)
    reval_examined: int = 0
    reval_changed: int = 0

    # GC
    vectors_removed: int = 0

    # LEARN
    skills_extracted: int = 0

    # Aggregate
    compression_ratio: float = 0.0
    active_before: int = 0
    active_after: int = 0
    duration_sec: float = 0.0
    errors: list[str] = field(default_factory=list)
