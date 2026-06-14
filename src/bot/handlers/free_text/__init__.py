"""Free-text pipeline — backward-compatible re-export facade.

All implementation in _core.py. This package exists so future splits
(_stages.py, _executors.py, _dispatch.py etc.) can be imported here
without breaking existing callers.
"""

from src.bot.handlers.free_text._core import (
    _dispatch,
    _save_intent_context,
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
    "_dispatch",
    "_save_intent_context",
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
