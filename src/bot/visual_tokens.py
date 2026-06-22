"""Centralized emoji tokens for all bot handlers.
Import and reuse instead of inline dicts scattered across handlers/.
Usage: from src.bot.visual_tokens import SENTIMENT_EMOJI
ponytail: single source of truth, no abstractions.
"""

SENTIMENT_EMOJI: dict[str, str] = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}
RISK_EMOJI: dict[str, str] = {"high": "🔴", "medium": "🟡", "low": "🟢"}
ENTITY_KIND_EMOJI: dict[str, str] = {
    "user": "👤",
    "group": "👥",
    "channel": "📢",
    "bot": "🤖",
}
TASK_STATUS_EMOJI: dict[str, str] = {
    "done": "✅",
    "cancelled": "❌",
    "open": "📋",
    "reminded": "⏰",
}
CONV_STATUS_EMOJI: dict[str, str] = {
    "active": "🟢",
    "waiting_reply": "🟡",
    "snoozed": "💤",
    "closed": "⚫",
    "archived": "⚪",
}
REL_STATUS_EMOJI: dict[str, str] = {
    "active": "🟢",
    "tension": "🟡",
    "resolved": "🔵",
    "distant": "⚪",
}
REL_PHASE_EMOJI: dict[str, str] = {"warming": "📈", "cooling": "📉", "stable": "📊"}
RELATION_EMOJI: dict[str, str] = {
    "cause": "🎯",
    "effect": "⚡",
    "contradicts": "⚠️",
    "supports": "✅",
    "continues": "➡️",
    "example_of": "📌",
    "resolves": "🔄",
}
SECURITY_AUDIT_EMOJI: dict[str, str] = {
    "ok": "✅",
    "warning": "⚠️",
    "critical": "❌",
    "info": "ℹ️",
    "secure": "🟢",
}
TIER_EMOJI: dict[str, str] = {"free": "🆓", "paid": "💰", "custom": "🔧", "local": "🖥️"}
