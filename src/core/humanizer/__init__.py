"""Humanizer — anti-AI детекция и очеловечивание текста."""

from .scorer import analyze_ai_score
from .humanizer import (
    humanize_text,
    humanize_response,
    humanize_deep,
    apply_anti_ai_mode,
    normalize_anti_ai_mode,
    record_humanizer_feedback,
    _cache_last_humanized,
    _pop_last_humanized,
    _preservation_check,
)
from .vocabulary import (
    BASE_AI_MARKERS,
    get_ai_markers,
    REPEAT_PENALTY,
    REPEAT_THRESHOLD,
    MAX_THEORETICAL_SCORE,
)
from .patterns import AI_PATTERNS, IDEAL_LENGTH_MIN, IDEAL_LENGTH_MAX, REPEAT_PENALTIES
from .stats import record_check, get_stats

__all__ = [
    "AI_PATTERNS",
    "BASE_AI_MARKERS",
    "IDEAL_LENGTH_MAX",
    "IDEAL_LENGTH_MIN",
    "MAX_THEORETICAL_SCORE",
    "REPEAT_PENALTIES",
    "REPEAT_PENALTY",
    "REPEAT_THRESHOLD",
    "_cache_last_humanized",
    "_pop_last_humanized",
    "_preservation_check",
    "analyze_ai_score",
    "apply_anti_ai_mode",
    "get_ai_markers",
    "get_stats",
    "humanize_deep",
    "humanize_response",
    "humanize_text",
    "normalize_anti_ai_mode",
    "record_check",
    "record_humanizer_feedback",
]
