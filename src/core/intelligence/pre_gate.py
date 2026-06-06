"""Pre-LLM gate — handles common patterns without calling LLM.

Loads pattern database from data/pre_gate_patterns.json at module import time.
Uses O(1) set lookups for exact matches and substring matching for multi-word patterns.
Regex is used only for URL/link spam detection (complex patterns).

Performance target: <1ms for 100+ patterns on 1 GHz CPU.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)

# ── Load pattern database ────────────────────────────────────────────────

_PATTERNS_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "data"
    / "pre_gate_patterns.json"
)


def _load_patterns() -> dict[str, list[str]]:
    """Load pre-gate patterns from JSON file. Returns empty dict on failure."""
    try:
        with open(_PATTERNS_PATH, "r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        # Filter out metadata keys (starting with _)
        return {
            k: v
            for k, v in data.items()
            if not k.startswith("_") and isinstance(v, list)
        }
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load pre_gate_patterns.json: %s", exc)
        return {}


# ── Pattern categories loaded at import time ────────────────────────────

_RAW_PATTERNS: dict[str, list[str]] = _load_patterns()

# Fast lookup structures built at import time
_exact_sets: dict[str, frozenset[str]] = {}  # category → frozenset of 1-2 word patterns
_substring_patterns: dict[
    str, list[str]
] = {}  # category → list of multi-word patterns for substring search

for _cat, _patterns in _RAW_PATTERNS.items():
    _exact = set()
    _substr = []
    for p in _patterns:
        stripped = p.strip().lower()
        # Patterns with <= 3 words → exact match set (fast O(1) lookup)
        # Patterns with > 3 words → substring match list
        if stripped.count(" ") <= 2:
            _exact.add(stripped)
        else:
            _substr.append(stripped)
    if _exact:
        _exact_sets[_cat] = frozenset(_exact)
    if _substr:
        _substring_patterns[_cat] = _substr

# ── URL / link regex for spam detection ──────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# ── Response templates by category ───────────────────────────────────────

_RESPONSE_TEMPLATES: dict[str, str | None] = {
    "time_greetings": "Привет! Чем могу помочь?",
    "farewells": "До связи! Если что — я здесь.",
    "gratitude": "Всегда пожалуйста! Рад помочь.",
    "agreement": None,  # Explicit None — let emoji stage handle it
    "disagreement": "Понял, не буду мешать.",  # Only for explicit rejection
    "simple_questions": None,  # Pass through to LLM for better answers
    "simple_commands": None,  # Pass through to LLM for action execution
    "laughter": "😂",
    "surprise": "😮",
    "sympathy": "Понимаю…",
    "spam_profanity": None,  # Silently ignore, or could return a warning
}

# ── Disagreement threshold: short rejection patterns that should trigger a response ──

_STRONG_DISAGREEMENT: frozenset[str] = frozenset(
    {
        "отстань",
        "отвали",
        "отойди",
        "отвяжись",
        "замолчи",
        "заткнись",
        "хватит",
        "прекрати",
        "перестань",
        "отмена",
        "стоп",
    }
)

# ── Affirmation patterns that should NOT trigger (handled by smart_reply emoji stage) ──

_AFFIRMATIVE_CATEGORIES: frozenset[str] = frozenset({"agreement"})

_NEGATIVE_RESPONSE_MAP: dict[str, str | None] = {
    "disagreement": "Понял, не буду мешать.",
}

for _cat, _resp in _RESPONSE_TEMPLATES.items():
    if _resp is not None:
        _NEGATIVE_RESPONSE_MAP[_cat] = _resp


def check_pre_gate(text: str) -> str | None:
    """Return a pre-canned response if text matches a known pattern, else None.

    Matching strategy (in order):
    1. Exact match against 1-2 word patterns (O(1) frozenset lookup)
    2. Substring match for multi-word patterns
    3. Regex for spam/URL detection (only if extended mode)

    Feature flag: settings.pre_gate_extended (default True) enables all categories.
    When False, only legacy categories (greetings, farewells, affirmative, negative) are checked.
    """
    if not text:
        return None

    t = text.strip().lower().rstrip(".!?")

    # ── Determine active categories based on feature flag ───────────────
    _extended: bool = getattr(settings, "pre_gate_extended", True)

    # Always check these categories regardless of flag
    _core_categories = ("time_greetings", "farewells", "agreement", "disagreement")

    # Extended categories (enabled by default)
    _extended_categories = (
        "gratitude",
        "simple_questions",
        "simple_commands",
        "laughter",
        "surprise",
        "sympathy",
    )

    categories_to_check: list[str] = list(_core_categories)
    if _extended:
        categories_to_check.extend(_extended_categories)

    # ── Phase 1: Exact match (O(1)) ────────────────────────────────────
    for cat in categories_to_check:
        exact_set = _exact_sets.get(cat)
        if exact_set and t in exact_set:
            return _resolve_response(cat, t)

    # ── Phase 2: Substring match for multi-word patterns ──────────────
    for cat in categories_to_check:
        substr_list = _substring_patterns.get(cat)
        if substr_list:
            if any(p in t for p in substr_list):
                return _resolve_response(cat, t)

    # ── Phase 3: Spam/profanity via regex (extended only) ─────────────
    if _extended:
        spam_set = _exact_sets.get("spam_profanity")
        if spam_set:
            for spam_word in spam_set:
                if spam_word in t:
                    # Check for standalone URLs without context
                    urls = _URL_RE.findall(t)
                    if urls and len(t) < 80:
                        # Very short message with only URLs → likely spam
                        return None  # Silently ignore; don't engage

    # ── Phase 4: Check standalone URL (spam detection) ────────────────
    if _extended:
        stripped_urls = _URL_RE.findall(t)
        if stripped_urls:
            # Pure URL message (no other meaningful text) → ignore
            remaining = _URL_RE.sub("", t).strip().rstrip(".!?")
            if not remaining or len(remaining) < 5:
                return None

    return None


def _resolve_response(category: str, _text: str) -> str | None:
    """Map category to response, with special handling for certain categories."""

    # Affirmative patterns — return None to let emoji/smart_reply handle it
    if category in _AFFIRMATIVE_CATEGORIES:
        return None

    # Disagreement — only respond to strong rejection patterns
    if category == "disagreement":
        if _text not in _STRONG_DISAGREEMENT:
            return None
        return _NEGATIVE_RESPONSE_MAP.get(category, "Понял, не буду мешать.")

    # Categories with explicit None responses → pass through
    template = _RESPONSE_TEMPLATES.get(category)
    if template is None:
        return None

    return template


# ── Utility: get pattern count for diagnostics ───────────────────────────


def get_pattern_stats() -> dict[str, int]:
    """Return category → pattern count for diagnostics/monitoring."""
    return {cat: len(patterns) for cat, patterns in _RAW_PATTERNS.items()}
