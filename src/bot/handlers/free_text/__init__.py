"""Free-text pipeline — backward-compatible re-export facade.

Implementation split across:
- _core.py: pipeline stages, intent dispatch, routing
- _dag.py: DAG dispatch, dedup cache
- _confirm.py: tool/intent confirmation callbacks
- _voice.py: voice transcription handlers
- _media.py: photo/video media handlers
- _singalong.py: singalong (lyrics matching)
"""

from src.bot.handlers.free_text._core import (
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
    execute_fast_route,
    execute_instant,
    execute_maestro,
    INTENT_HANDLERS,
)

from src.bot.handlers.free_text._dag import (
    _dag_dispatch,
    _run_dag_level,
)

from src.bot.handlers.free_text._confirm import (
    confirm_router,
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
    "_run_dag_level",
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
