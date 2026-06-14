"""Backward-compatible re-export stub. All logic in src.bot.handlers.free_text/."""

from src.bot.handlers.free_text import (
    _dag_dispatch,
    _detect_context_hint,
    _dispatch,
    _looks_like_send_request,
    _safe_for_deep_humanize,
    _save_intent_context,
    _time_of_day_greeting,
    check_contact_rules,
    check_followup,
    check_instructions,
    check_persona,
    confirm_router,
    execute_fast_route,
    execute_instant,
    execute_maestro,
    register_cleanup_timer,
)

__all__ = [
    "_dag_dispatch",
    "_detect_context_hint",
    "_dispatch",
    "_looks_like_send_request",
    "_safe_for_deep_humanize",
    "_save_intent_context",
    "_time_of_day_greeting",
    "check_contact_rules",
    "check_followup",
    "check_instructions",
    "check_persona",
    "confirm_router",
    "execute_fast_route",
    "execute_instant",
    "execute_maestro",
    "register_cleanup_timer",
]
