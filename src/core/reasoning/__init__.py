"""Reasoning & Planning — CoT Engine + HTN Planner + Meta-Reasoner for Phase 2.

Подсистема логического рассуждения, планирования и мета-оценки:
- CoTEngine: пошаговые рассуждения с самокоррекцией и вызовом инструментов.
- HTNPlanner: иерархическое планирование задач (Hierarchical Task Network).
- MetaReasoner: оценка качества рассуждений, пробелы, делегирование.
"""

from src.core.reasoning.cot_engine import CoTEngine, ReasoningStep, ReasoningTrace

# HTNPlanner — реализуется отдельно (Phase 2)
try:
    from src.core.reasoning.htn_planner import HTNPlanner, Plan, PlanStep  # type: ignore[import-untyped]
except ImportError:
    HTNPlanner = None  # type: ignore[assignment]
    Plan = None  # type: ignore[assignment]
    PlanStep = None  # type: ignore[assignment]

# MetaReasoner — реализуется отдельно (Phase 2)
try:
    from src.core.reasoning.meta_reasoner import MetaReasoner, MetaEvaluation  # type: ignore[import-untyped]
except ImportError:
    MetaReasoner = None  # type: ignore[assignment]
    MetaEvaluation = None  # type: ignore[assignment]

__all__ = [
    "CoTEngine",
    "ReasoningStep",
    "ReasoningTrace",
    "HTNPlanner",
    "Plan",
    "PlanStep",
    "MetaReasoner",
    "MetaEvaluation",
]
