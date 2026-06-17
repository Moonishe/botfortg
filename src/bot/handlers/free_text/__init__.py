"""Free-text pipeline — backward-compatible re-export facade.

All implementation in _core.py. This package exists so future splits
(_stages.py, _executors.py, _dispatch.py etc.) can be imported here
without breaking existing callers.
"""

from src.bot.handlers.free_text._core import (
    _dag_dispatch,
    _detect_context_hint,
    _dispatch,
    _execute_intent,
    _extract_contact_hint,
    _looks_like_send_request,
    _safe_for_deep_humanize,
    _save_intent_context,
    _time_of_day_greeting,
    check_contact_rules,
    check_followup,
    check_instructions,
    check_persona,
    CLASSIC_INTENT_HANDLERS,
    confirm_router,
    execute_fast_route,
    execute_instant,
    execute_maestro,
    INTENT_HANDLERS,
    register_cleanup_timer,
)

__all__ = [
    "CLASSIC_INTENT_HANDLERS",
    "INTENT_HANDLERS",
    "_dag_dispatch",
    "_detect_context_hint",
    "_dispatch",
    "_execute_intent",
    "_extract_contact_hint",
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
