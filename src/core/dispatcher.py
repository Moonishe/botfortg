"""Core dispatch result types — kept in src.core so both src.bot and src.core can
import them without crossing the core→bot boundary.

The actual dispatcher facade lives in src.bot.dispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DispatchResult:
    """Standardised output from every dispatch leg."""

    handled: bool = True
    response_text: str = ""
    route_mode: str = ""  # "pre_gate", "instant", "fast_route", "maestro", "unknown"
    success: bool = True
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    skip_humanize: bool = False
    skip_trajectory: bool = False
    skip_session_log: bool = False
    skip_post_turn: bool = False


@dataclass
class MaestroResult:
    """Standardised output from execute_maestro (Issue 3 fix — was bool|dict)."""

    handled: bool = True
    response_text: str = ""
    used_skills: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    route_mode: str = "maestro"
