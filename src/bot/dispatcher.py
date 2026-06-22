"""Bot-level dispatcher facade.

Orchestrates free-text dispatch legs (which live in src.bot.handlers.free_text).
This module lives in src.bot so that src.core remains free of bot-layer imports.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.bot.handlers.free_text._core import (
    execute_fast_route,
    execute_instant,
    execute_maestro,
)
from src.bot.handlers.free_text_common import (
    _fire_record_trajectory,
    _post_turn_optimize,
    safe_answer,
)
from src.core.dispatcher import DispatchResult
from src.core.infra.hooks import hooks
from src.core.infra.key_guard import safe_str
from src.core.infra.task_manager import track_ff
from src.core.infra.text_sanitizer import sanitize_html
from src.core.infra.timeutil import now_in_tz
from src.core.intelligence.pre_gate import check_pre_gate
from src.core.memory import conversation_context as ctx_store
from src.core.scheduling.session_logger import (
    log_assistant_response,
    log_user_message,
)

logger = logging.getLogger(__name__)


class UnifiedDispatcher:
    """Facade over execute_instant / execute_fast_route / execute_maestro.

    When use_unified_dispatcher=True in settings, _process_text calls
    dispatcher.dispatch() instead of the if/elif chain. The dispatcher
    runs shared pre/post hooks once, then delegates to the correct leg
    with _via_dispatcher=True to skip the leg's own duplicated hooks.
    """

    def __init__(self) -> None:
        pass

    async def dispatch(
        self,
        *,
        raw: str,
        plan: Any,
        provider: Any,
        message: Any,
        state: Any,
        userbot_manager: Any,
        owner_telegram_id: int,
        tz_name: str,
        history_block: str,
        turn_started: float,
        injected_style: str | None = None,
    ) -> DispatchResult:
        """Run pre-hooks → delegate to correct leg → run post-hooks."""
        # Defensive: plan=None → fallback to maestro
        if plan is None:
            logger.warning("dispatch() called with plan=None, falling back to maestro")
            try:
                await safe_answer(
                    message, "⚠️ Внутренняя ошибка маршрутизации. Попробуй ещё раз."
                )
            except Exception:
                logger.warning(
                    "dispatcher: failed to send routing error message", exc_info=True
                )
            return DispatchResult(
                handled=False, route_mode="unknown", error="plan is None"
            )

        # ── PRE-HOOKS (run once) ──
        # 1. Plugin hooks
        track_ff(
            asyncio.create_task(
                hooks.emit("on_message_received", user_id=owner_telegram_id, text=raw)
            )
        )
        # 2. Log user message
        track_ff(asyncio.create_task(log_user_message(owner_telegram_id, raw)))
        # 3. Pre-gate check (greetings, farewells)
        gate_resp = check_pre_gate(raw)
        if gate_resp:
            humanized = gate_resp  # pre-gate responses are pre-formatted
            await safe_answer(message, sanitize_html(humanized))
            result = DispatchResult(
                handled=True,
                response_text=humanized,
                route_mode="pre_gate",
                skip_humanize=True,
            )
            await self._post_process(
                result, raw, message, owner_telegram_id, turn_started
            )
            return result

        # ── DELEGATE ──
        response_mode = getattr(plan, "response_mode", "maestro")

        if response_mode == "instant":
            handled = await execute_instant(
                plan,
                message,
                raw,
                owner_telegram_id,
                turn_started,
                tz_name=tz_name,
                _via_dispatcher=True,
            )
            result = DispatchResult(
                handled=handled,
                route_mode="instant",
                response_text=getattr(plan, "final_response", ""),
            )
        elif response_mode == "fast_route":
            now_local_str = now_in_tz(tz_name).strftime("%Y-%m-%d %H:%M")
            try:
                handled = await execute_fast_route(
                    raw,
                    plan,
                    provider,
                    message,
                    state,
                    userbot_manager,
                    tz_name,
                    owner_telegram_id,
                    history_block,
                    turn_started,
                    now_local_str,
                    _via_dispatcher=True,
                )
            except Exception as e:
                logger.exception("UnifiedDispatcher fast_route failed")
                return DispatchResult(
                    handled=False,
                    route_mode="fast_route",
                    success=False,
                    error=safe_str(e)[:4000],
                    skip_humanize=True,
                    skip_session_log=True,
                )
            result = DispatchResult(
                handled=handled,
                route_mode="fast_route",
                skip_humanize=True,
                skip_session_log=True,
            )
        elif response_mode == "maestro":
            mr = await execute_maestro(
                raw,
                plan,
                provider,
                message,
                state,
                userbot_manager,
                tz_name,
                owner_telegram_id,
                history_block,
                turn_started,
                injected_style=injected_style,
                _via_dispatcher=True,
            )
            result = DispatchResult(
                handled=mr.handled,
                route_mode=mr.route_mode or "maestro",
                response_text=mr.response_text,
                extra={
                    "used_skills": mr.used_skills,
                    "trace": mr.trace,
                },
            )
        else:
            result = DispatchResult(
                handled=False,
                route_mode="unknown",
                success=False,
                error=f"Unknown response_mode: {response_mode!r}",
            )

        # ── POST-HOOKS (run once) ──
        # Always run post-process for telemetry — even on failure (records error)
        await self._post_process(result, raw, message, owner_telegram_id, turn_started)

        return result

    async def _post_process(
        self,
        result: DispatchResult,
        raw: str,
        message: Any,
        owner_telegram_id: int,
        turn_started: float,
    ) -> None:
        """Run post-processing hooks (trajectory, session log, etc.)."""
        # Trajectory
        if not result.skip_trajectory:
            _fire_record_trajectory(
                owner_telegram_id,
                request_text=raw,
                route_mode=result.route_mode,
                response_text=result.response_text,
                success=result.success,
                error=result.error,
                latency_ms=int((time.monotonic() - turn_started) * 1000),
            )

        # Conversation context
        if result.response_text:
            try:
                await ctx_store.add_turn(
                    message.from_user.id, raw[:200], result.response_text[:400]
                )
            except Exception:
                logger.debug("ctx_store.add_turn failed", exc_info=True)

        # Session log
        if not result.skip_session_log and result.response_text:
            track_ff(
                asyncio.create_task(
                    log_assistant_response(message.from_user.id, result.response_text)
                )
            )

        # Post-turn optimization
        if not result.skip_post_turn:
            await _post_turn_optimize(owner_telegram_id, raw, result.response_text)

        # Plugin hooks
        if result.route_mode.startswith("maestro"):
            track_ff(
                asyncio.create_task(
                    hooks.emit(
                        "on_message_post_maestro",
                        user_id=owner_telegram_id,
                        input=raw,
                        response=result.response_text,
                    )
                )
            )


# Singleton
dispatcher = UnifiedDispatcher()
