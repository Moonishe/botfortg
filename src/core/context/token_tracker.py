"""Token budget tracker — approximate token counting for prompt management.

Uses a fast word-based heuristic (words × 1.3 ≈ tokens for Cyrillic/Latin).
Accurate enough for budget enforcement without the cost of a full tokenizer.
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# Budget thresholds (percentage of model max tokens)
BUDGET_MILD = 0.50  # light formatting at 50%
BUDGET_SUMMARY = 0.75  # LLM summarisation at 75%
BUDGET_AGGRESSIVE = 0.85  # Mermaid offload at 85%

# Default model max tokens (conservative — works for most models)
DEFAULT_MAX_TOKENS = 4096


def estimate_tokens(text: str) -> int:
    """Fast token count estimate with improved CJK/emoji heuristic.

    CJK chars ≈ 1 token each, emoji ≈ 2 tokens, words ≈ 1.3 tokens.
    No tiktoken dependency. Error margin ~10-15% for mixed text.
    """
    if not text:
        return 0
    # Count CJK characters (Chinese, Japanese, Korean)
    cjk_count = sum(
        1
        for c in text
        if "\u4e00" <= c <= "\u9fff"
        or "\u3040" <= c <= "\u30ff"
        or "\uac00" <= c <= "\ud7af"
    )
    # Count emoji (basic ranges)
    emoji_count = sum(
        1
        for c in text
        if "\U0001F000" <= c <= "\U0001FFFF" or "\u2600" <= c <= "\u27BF"
    )
    # Word count for remaining text
    words = len(re.findall(r"\w+", text))
    remaining = len(text) - cjk_count - emoji_count
    # CJK: ~1 token/char, emoji: ~2 tokens, words: ~1.3 tokens/word
    return int(cjk_count + emoji_count * 2 + max(words * 1.3, remaining * 0.25))


def count_prompt_tokens(
    system_prompt: str = "",
    user_prompt: str = "",
    history: list[dict] | None = None,
) -> int:
    """Estimate total tokens for an LLM prompt including history."""
    total = estimate_tokens(system_prompt) + estimate_tokens(user_prompt)
    if history:
        for msg in history:
            total += estimate_tokens(str(msg.get("content", "")))
            total += 4  # role + formatting overhead per message
    return total


def get_budget_stage(
    current_tokens: int,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> tuple[str, float]:
    """Determine which compression stage to apply.

    Returns (stage, fill_ratio).
    Stages: "ok" | "format" | "summary" | "mermaid"
    """
    ratio = current_tokens / max_tokens

    if ratio >= BUDGET_AGGRESSIVE:
        return ("mermaid", round(ratio, 2))
    if ratio >= BUDGET_SUMMARY:
        return ("summary", round(ratio, 2))
    if ratio >= BUDGET_MILD:
        return ("format", round(ratio, 2))
    return ("ok", round(ratio, 2))


__all__ = [
    "BUDGET_AGGRESSIVE",
    "BUDGET_MILD",
    "BUDGET_SUMMARY",
    "DEFAULT_MAX_TOKENS",
    "count_prompt_tokens",
    "estimate_tokens",
    "get_budget_stage",
]
