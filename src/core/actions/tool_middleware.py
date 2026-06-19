"""Tool Middleware Chain — pre/post hooks for tool execution.

A lightweight pipeline: sync pre-hooks → execute → async post-hooks.
Each middleware can inspect/block/modify context.

Built-in middlewares are registered by :func:`init_default_middlewares`
(at import time) via :func:`register`.

Usage::

    from src.core.actions.tool_middleware import middleware_chain

    middleware_chain.register(
        name="metrics", priority=90,
        post=lambda ctx: _record_latency(ctx),
    )
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """Mutable context passed through the middleware pipeline.

    Pre-hooks receive and return ``ToolContext``, allowing them to
    inspect, modify or block execution.  Post-hooks receive the
    context *after* the handler has run (``ctx.result`` is populated).
    """

    tool_name: str
    params: dict[str, Any]
    result: dict[str, Any] | None = None
    blocked: bool = False
    block_reason: str = ""
    started_at: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


PreHook = Callable[
    [ToolContext], ToolContext | None | Coroutine[Any, Any, ToolContext | None]
]
PostHook = Callable[[ToolContext], Coroutine[Any, Any, None]]


class MiddlewareChain:
    """Ordered pipeline of pre/post hooks for tool execution.

    Hooks are ordered by *priority* (lower → runs first for pre-hooks,
    higher → runs first for post-hooks).  If a pre-hook returns ``None``
    the pipeline is short-circuited and the tool is *blocked*.
    """

    def __init__(self) -> None:
        self._mw: list[tuple[str, int, PreHook | None, PostHook | None]] = []
        self._defaults_initialized: bool = False

    @property
    def registered(self) -> list[str]:
        """Return names of all registered middlewares (ordered)."""
        return [name for name, *_ in self._mw]

    def register(
        self,
        *,
        name: str,
        priority: int = 50,
        pre: PreHook | None = None,
        post: PostHook | None = None,
    ) -> None:
        """Register a middleware hook.

        Args:
            name: Unique middleware name (used in error messages).
            priority: Lower runs first for pre-hooks (default 50).
            pre: Optional synchronous pre-hook — receives ``ToolContext``
                and must return it (possibly modified) or ``None`` to block.
            post: Optional async post-hook — receives ``ToolContext`` after
                the handler has returned.
        """
        if not pre and not post:
            logger.warning("Middleware %r has neither pre nor post — ignored", name)
            return
        if name in (n for n, *_ in self._mw):
            logger.warning("Middleware %r already registered — ignored", name)
            return
        # Build a new list and atomically replace — avoids race with wrap()
        # which iterates over a snapshot of self._mw.
        new_mw = list(self._mw)
        new_mw.append((name, priority, pre, post))
        new_mw.sort(key=lambda x: x[1])
        self._mw = new_mw

    async def wrap(
        self,
        tool_name: str,
        params: dict[str, Any],
        execute_fn: Callable[..., Coroutine[Any, Any, dict]],
    ) -> dict[str, Any]:
        """Execute *execute_fn* wrapped in all registered middleware hooks.

        Args:
            tool_name: Name of the tool (for context).
            params: Parameters to pass to *execute_fn* (may be modified by
                pre-hooks).
            execute_fn: Async callable that accepts ``(tool_name, params)``
                and returns a ``dict`` result.

        Returns:
            The result dict as returned by *execute_fn* (or a middleware),
            or ``{"error": ...}`` if blocked by a pre-hook or an exception.
        """
        ctx = ToolContext(
            tool_name=tool_name, params=params, started_at=time.monotonic()
        )

        # Snapshot the middleware list to avoid race with concurrent register().
        mw = self._mw

        # ── Pre-hooks (sync or async) ──
        for _name, _, pre, _ in mw:
            if pre is None:
                continue
            try:
                if inspect.iscoroutinefunction(pre):
                    new_ctx = await pre(ctx)
                else:
                    new_ctx = pre(ctx)
            except Exception:
                logger.exception("Pre-hook %r failed", _name)
                continue
            if new_ctx is None:
                ctx.blocked = True
                if not ctx.block_reason:
                    ctx.block_reason = f"Blocked by '{_name}'"
                return {
                    "ok": False,
                    "error": ctx.block_reason,
                    "blocked_by": "middleware",
                }
            if not isinstance(new_ctx, ToolContext):
                logger.warning("Pre-hook %r returned non-ToolContext — ignored", _name)
                continue
            ctx = new_ctx

        # ── Handler execution ──
        try:
            ctx.result = await execute_fn(tool_name, ctx.params)
        except Exception as exc:
            ctx.result = {"ok": False, "error": str(exc)}

        # ── Post-hooks (async) ──
        for _name, _, _, post in mw:
            if post is None:
                continue
            try:
                await post(ctx)
            except Exception:
                logger.exception("Post-hook %r failed", _name)

        return ctx.result if ctx.result is not None else {"ok": True}


# ── Module-level singleton ──────────────────────────────────────

middleware_chain = MiddlewareChain()


# ===================================================================
# Circuit Breaker
# ===================================================================


@dataclass
class CBState:
    """Internal state of a single tool's circuit breaker."""

    failures: int = 0
    state: str = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
    opened_at: float = 0.0
    _probe_in_flight: bool = (
        False  # ponytail: simple bool, no semaphore; prevents multi-probe race
    )


class ToolCircuitBreaker:
    """Per-tool circuit breaker that blocks calls after consecutive failures.

    States:
        CLOSED     — normal operation, counting failures
        OPEN       — >FAILURE_THRESHOLD consecutive failures, blocked for COOLDOWN
        HALF_OPEN  — cooldown expired, a single probe call is permitted

    If the probe succeeds → back to CLOSED (failures=0).
    If the probe fails    → back to OPEN (cooldown restarts).

    Usage (via middleware factory)::

        pre, post = _build_circuit_breaker()
        chain.register(name="circuit_breaker", priority=30, pre=pre, post=post)
    """

    _states: dict[str, CBState] = {}
    _lock: asyncio.Lock = asyncio.Lock()
    FAILURE_THRESHOLD: int = 5
    COOLDOWN_SECONDS: float = 120.0

    @classmethod
    async def check(cls, tool_name: str) -> bool:
        """Check whether *tool_name* is allowed to execute.

        Returns:
            ``True`` if the call is permitted, ``False`` if blocked (OPEN).
        """
        async with cls._lock:
            state = cls._states.get(tool_name)
            if state is None:
                return True  # No failures recorded — circuit is implicitly CLOSED

            if state.state == "CLOSED":
                return True
            if state.state == "HALF_OPEN":
                # Only allow one probe call at a time
                if state._probe_in_flight:
                    return False
                state._probe_in_flight = True
                return True  # Allow probe call

            # state == OPEN
            elapsed = time.monotonic() - state.opened_at
            # Guard against pre-reboot monotonic timestamps: negative elapsed
            # means ``opened_at`` is from a previous process — treat as expired.
            if elapsed < 0 or elapsed >= cls.COOLDOWN_SECONDS:
                old_state = state.state
                state.state = "HALF_OPEN"
                state._probe_in_flight = True  # this transition IS the probe flight
                from src.core.events.event_bus import event_bus, CIRCUIT_STATE

                await event_bus.emit(
                    CIRCUIT_STATE,
                    tool_name=tool_name,
                    from_state=old_state,
                    to_state="HALF_OPEN",
                    reason="cooldown_expired",
                    failures=state.failures,
                )
                logger.debug(
                    "Circuit for %r: OPEN → HALF_OPEN (%.1fs elapsed)",
                    tool_name,
                    elapsed,
                )
                return True  # Allow probe call

            return False  # Still in cooldown — block

    @classmethod
    async def record_success(cls, tool_name: str) -> None:
        """Record a successful call — reset failures (CLOSED)."""
        async with cls._lock:
            state = cls._states.get(tool_name)
            if state is None:
                return  # No state — nothing to update
            old_state = state.state
            state.failures = 0
            state.state = "CLOSED"
            state._probe_in_flight = False
            if old_state != "CLOSED":
                from src.core.events.event_bus import event_bus, CIRCUIT_STATE

                await event_bus.emit(
                    CIRCUIT_STATE,
                    tool_name=tool_name,
                    from_state=old_state,
                    to_state="CLOSED",
                    reason="probe_success",
                    failures=0,
                )
                logger.info(
                    "Circuit for %r: %s → CLOSED (successful probe)",
                    tool_name,
                    old_state,
                )

    @classmethod
    async def record_failure(cls, tool_name: str) -> None:
        """Record a failed call and trip the circuit if threshold exceeded."""
        async with cls._lock:
            state = cls._states.get(tool_name)
            if state is None:
                state = CBState()
                cls._states[tool_name] = state

            state.failures += 1
            state._probe_in_flight = False  # probe resolved (either success or failure)

            if state.state == "HALF_OPEN":
                # Probe failed — back to OPEN
                old_state = state.state
                state.state = "OPEN"
                state.opened_at = time.monotonic()
                from src.core.events.event_bus import event_bus, CIRCUIT_STATE

                await event_bus.emit(
                    CIRCUIT_STATE,
                    tool_name=tool_name,
                    from_state=old_state,
                    to_state="OPEN",
                    reason="probe_failure",
                    failures=state.failures,
                )
                logger.warning(
                    "Circuit for %r: HALF_OPEN probe failed — back to OPEN "
                    "(failures=%d)",
                    tool_name,
                    state.failures,
                )
            elif state.failures >= cls.FAILURE_THRESHOLD and state.state != "OPEN":
                old_state = state.state
                state.state = "OPEN"
                state.opened_at = time.monotonic()
                from src.core.events.event_bus import event_bus, CIRCUIT_STATE

                await event_bus.emit(
                    CIRCUIT_STATE,
                    tool_name=tool_name,
                    from_state=old_state,
                    to_state="OPEN",
                    reason="failure_threshold",
                    failures=state.failures,
                )
                logger.warning(
                    "Circuit for %r: tripped OPEN after %d failures",
                    tool_name,
                    state.failures,
                )
            else:
                # No transition yet, but push updated failure count to telemetry
                from src.core.events.event_bus import event_bus, CIRCUIT_STATE

                await event_bus.emit(
                    CIRCUIT_STATE,
                    tool_name=tool_name,
                    from_state=state.state,
                    to_state=state.state,  # no transition
                    reason="failure_count",
                    failures=state.failures,
                )

    @classmethod
    async def reset(cls, tool_name: str | None = None) -> None:
        """Reset circuit state for *tool_name* (or all tools if None/empty).

        Useful for testing.
        """
        from src.core.events.event_bus import event_bus, CIRCUIT_STATE

        async with cls._lock:
            if not tool_name:
                for name in list(cls._states.keys()):
                    old_state = cls._states[name].state
                    cls._states[name]._probe_in_flight = False
                    await event_bus.emit(
                        CIRCUIT_STATE,
                        tool_name=name,
                        from_state=old_state,
                        to_state="CLOSED",
                        reason="manual_reset",
                        failures=0,
                    )
                cls._states.clear()
            else:
                old = cls._states.pop(tool_name, None)
                if old is not None:
                    old._probe_in_flight = (
                        False  # ponytail: reset clears the probe flight
                    )
                    await event_bus.emit(
                        CIRCUIT_STATE,
                        tool_name=tool_name,
                        from_state=old.state,
                        to_state="CLOSED",
                        reason="manual_reset",
                        failures=0,
                    )

    @classmethod
    async def get_state(cls, tool_name: str) -> CBState | None:
        """Return a copy of the circuit state for *tool_name*, or ``None``."""
        async with cls._lock:
            state = cls._states.get(tool_name)
            if state is None:
                return None
            return CBState(
                failures=state.failures,
                state=state.state,
                opened_at=state.opened_at,
                _probe_in_flight=state._probe_in_flight,
            )

    @classmethod
    async def get_all_states(cls) -> dict[str, CBState]:
        """Return copies of all circuit states."""
        async with cls._lock:
            return {
                name: CBState(
                    failures=s.failures,
                    state=s.state,
                    opened_at=s.opened_at,
                    _probe_in_flight=s._probe_in_flight,
                )
                for name, s in cls._states.items()
            }

    @classmethod
    async def _push_full_state(cls) -> None:
        """Emit a CIRCUIT_STATE snapshot for every currently tracked tool.

        Called once at startup so that CircuitTelemetry can initialise its
        ``_current_states`` cache without importing ToolCircuitBreaker.
        """
        from src.core.events.event_bus import event_bus, CIRCUIT_STATE

        async with cls._lock:
            for tool_name, state in cls._states.items():
                await event_bus.emit(
                    CIRCUIT_STATE,
                    tool_name=tool_name,
                    from_state=state.state,
                    to_state=state.state,  # snapshot — no transition
                    reason="startup_snapshot",
                    failures=state.failures,
                )

    @classmethod
    async def capture_state(cls) -> dict[str, dict]:
        """Return a serializable snapshot of all circuit breaker states.

        Public method for snapshot engine — avoids direct ``_states``/``_lock`` access.
        """
        async with cls._lock:
            return {
                tool_name: {
                    "failures": state.failures,
                    "state": state.state,
                    "opened_at": state.opened_at,
                    "_probe_in_flight": state._probe_in_flight,
                }
                for tool_name, state in cls._states.items()
            }

    @classmethod
    async def restore_state(cls, data: dict) -> None:
        """Restore circuit breaker states from a snapshot dict.

        Public method for snapshot engine — avoids direct ``_states``/``_lock`` access.
        Accepts pre-processed data (caller handles cooldown expiry, clamping, etc.).
        """
        async with cls._lock:
            for tool_name, state_data in data.items():
                cls._states[tool_name] = CBState(
                    failures=int(state_data.get("failures", 0)),
                    state=str(state_data.get("state", "CLOSED")),
                    opened_at=float(state_data.get("opened_at", 0.0)),
                    _probe_in_flight=bool(state_data.get("_probe_in_flight", False)),
                )

    @classmethod
    async def get_cooldown_remaining(cls, tool_name: str) -> float:
        """Return seconds remaining for OPEN circuits, 0 for CLOSED/HALF_OPEN.

        Returns 0.0 if the circuit is not OPEN, expired, or ``opened_at``
        is stale (pre-reboot monotonic timestamp).
        """
        async with cls._lock:
            state = cls._states.get(tool_name)
            if state is None or state.state != "OPEN":
                return 0.0
            elapsed = time.monotonic() - state.opened_at
            # Negative elapsed → stale timestamp → treat as expired
            if elapsed < 0:
                return 0.0
            remaining = cls.COOLDOWN_SECONDS - elapsed
            return max(0.0, round(remaining, 3))

    @classmethod
    async def cleanup_stale(cls) -> int:
        """Remove CLOSED entries with zero failures (default state).

        These entries are re-created on next failure — keeping them is
        harmless but grows the dict unboundedly when tools are renamed
        or removed.  Called periodically from the global cleanup loop.

        Returns:
            Number of entries removed.
        """
        async with cls._lock:
            stale = [
                name
                for name, s in cls._states.items()
                if s.state == "CLOSED" and s.failures == 0
            ]
            for name in stale:
                del cls._states[name]
        if stale:
            logger.debug(
                "ToolCircuitBreaker cleanup_stale: removed %d idle entries (%d remain)",
                len(stale),
                len(cls._states),
            )
        return len(stale)


class DecisionRepairGuard:
    """Decision-repair: ≥3 fails in ≤5 steps → stash repaired decision for next LLM turn.

    Sibling to ToolCircuitBreaker (reuses CBState-style failure counter).
    In-memory only (no DB on request path). Bounded deque prevents overflow.
    Gated by settings.reward_loop_enabled + settings.decision_repair_failure_threshold.
    """

    _failures: dict[str, deque] = {}  # signature → deque of timestamps
    _stash: dict[int, str] = {}  # telegram_id → stashed repair hint

    FAILURE_THRESHOLD: int = 3  # overridden by settings at runtime
    STEP_WINDOW: int = 5  # overridden by settings at runtime

    @classmethod
    def _get_threshold(cls) -> int:
        if settings.reward_loop_enabled:
            return settings.decision_repair_failure_threshold
        return cls.FAILURE_THRESHOLD

    @classmethod
    def _get_window(cls) -> int:
        if settings.reward_loop_enabled:
            return settings.decision_repair_step_window
        return cls.STEP_WINDOW

    @classmethod
    async def bump_failure(cls, signature: str) -> bool:
        """Returns True if threshold reached (≥N fails in ≤W steps)."""
        if not settings.reward_loop_enabled:
            return False
        window = cls._get_window()
        if window <= 0:
            return False
        if signature not in cls._failures:
            cls._failures[signature] = deque(maxlen=window)

        now = time.monotonic()
        # Evict stale entries before checking threshold
        d = cls._failures[signature]
        while d and (now - d[0]) > 300:  # 5 min TTL
            d.popleft()
        d.append(now)
        return len(d) >= cls._get_threshold()

    @classmethod
    def stash_repair(cls, telegram_id: int, hint: str) -> None:
        """Stash a repair hint for injection before next LLM turn."""
        if not settings.reward_loop_enabled:
            return
        cls._stash[telegram_id] = hint[:2000]  # bounded

    @classmethod
    def pop_stash(cls, telegram_id: int) -> str | None:
        """Pop and clear stashed hint. No fallback — prevents cross-user data leak."""
        return cls._stash.pop(telegram_id, None)

    @classmethod
    def cleanup_stale(cls) -> int:
        """Evict old failure entries. Called from periodic cleanup."""
        evicted = 0
        now = time.monotonic()
        for sig in list(cls._failures.keys()):
            d = cls._failures[sig]
            while d and (now - d[0]) > 300:  # 5 min TTL
                d.popleft()
                evicted += 1
            if not d:
                del cls._failures[sig]
        # ponytail: _stash is self-cleaning via pop_stash; single-owner ≤1 entry always.
        # No separate TTL needed. Add TTL if multi-user goes live and _stash outgrows 1 key.
        return evicted


def _build_circuit_breaker() -> tuple[PreHook, PostHook]:
    """Build a circuit-breaker middleware pair.

    Returns:
        ``(pre_hook, post_hook)`` — the pre-hook checks the circuit state
        and blocks calls when OPEN; the post-hook records success/failure
        based on the handler result.
    """
    cb_log = logging.getLogger("tool_middleware.circuit_breaker")

    async def _pre(ctx: ToolContext) -> ToolContext | None:
        if not await ToolCircuitBreaker.check(ctx.tool_name):
            cb_log.warning(
                "Circuit breaker blocked %r — circuit is OPEN", ctx.tool_name
            )
            ctx.blocked = True
            ctx.block_reason = (
                f"Circuit breaker: tool {ctx.tool_name!r} is OPEN (cooldown)"
            )
            return None
        return ctx

    async def _post(ctx: ToolContext) -> None:
        # Determine success/failure from the result dict.
        # Both handler exceptions and blocked calls produce {"error": ...}.
        if (
            ctx.result is not None
            and isinstance(ctx.result, dict)
            and "error" in ctx.result
        ):
            await ToolCircuitBreaker.record_failure(ctx.tool_name)
        elif ctx.result is not None:
            await ToolCircuitBreaker.record_success(ctx.tool_name)

    return _pre, _post


# ── Built-in middlewares ────────────────────────────────────────


def _build_tool_audit_post() -> PostHook:
    """Post-hook that logs tool execution summary."""
    log = logging.getLogger("tool_middleware.audit")

    async def _post(ctx: ToolContext) -> None:
        elapsed = time.monotonic() - ctx.started_at
        success = (
            ctx.result is not None
            and isinstance(ctx.result, dict)
            and "error" not in ctx.result
        )
        log.info(
            "Tool %r finished in %.2fs — success=%s",
            ctx.tool_name,
            elapsed,
            success,
        )

    return _post


def _build_tool_metrics_post() -> PostHook:
    """Post-hook that records latency in meta and ToolMetricsCollector.

    Writes ``ctx.meta["latency_seconds"]`` for downstream consumers and
    records the call into the global :class:`ToolMetricsCollector` singleton.
    """
    log = logging.getLogger("tool_middleware.metrics")

    async def _post(ctx: ToolContext) -> None:
        from src.core.observability.tool_metrics import tool_metrics

        elapsed = time.monotonic() - ctx.started_at
        latency_ms = round(elapsed * 1000, 3)
        ctx.meta["latency_seconds"] = round(elapsed, 4)
        ctx.meta["latency_ms"] = latency_ms

        success = (
            ctx.result is not None
            and isinstance(ctx.result, dict)
            and "error" not in ctx.result
        )
        await tool_metrics.record_call(
            tool_name=ctx.tool_name,
            latency_ms=latency_ms,
            success=success,
        )
        log.debug(
            "Tool %r latency: %.2fms success=%s",
            ctx.tool_name,
            latency_ms,
            success,
        )

        # perf.jsonl log (gated by settings.reward_loop_enabled)
        if settings.reward_loop_enabled:
            try:
                from datetime import datetime, timezone

                perf_logger = logging.getLogger("perf.jsonl")
                perf_logger.info(
                    json.dumps(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "component": "tool",
                            "tool": ctx.tool_name,
                            "latency_ms": latency_ms,
                            "success": success,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                )
            except Exception:
                pass

            # Decision-repair: bump failure counter on tool failure
            if not success:
                try:
                    err_code = (
                        ctx.result.get("error", "unknown")
                        if isinstance(ctx.result, dict)
                        else "unknown"
                    )
                    # ponytail: truncate + strip non-printable + mask keys
                    err_code = "".join(
                        c for c in str(err_code)[:100] if c.isprintable()
                    ).replace("`", "'")
                    from src.core.infra.key_guard import mask_keys as _mask_keys

                    err_code = _mask_keys(err_code)
                    sig = f"{ctx.tool_name}|{err_code}"
                    threshold_reached = await DecisionRepairGuard.bump_failure(sig)
                    if threshold_reached:
                        hint = (
                            f"Tool '{ctx.tool_name}' failed repeatedly (code: {err_code}). "
                            "Consider alternative approach."
                        )
                        # ponytail: settings.owner_telegram_id only (single-user).
                        # ctx.meta["telegram_id"] not yet populated; add when multi-user goes live.
                        DecisionRepairGuard.stash_repair(
                            ctx.meta.get("telegram_id", settings.owner_telegram_id),
                            hint,
                        )
                except Exception:
                    pass

    return _post


def _build_input_validator() -> PreHook:
    """Pre-hook that validates tool params against the tool's ``input_schema``.

    Uses ``jsonschema.validate()`` against the ``ToolSpec.input_schema``
    registered for the tool.  If validation fails the tool is blocked and
    an error message is returned.

    Lazy-imports ``jsonschema`` and ``tool_registry`` to avoid circular
    dependencies.

    .. note::

       ``jsonschema`` is an optional dependency.  If not installed, schema
       validation is **silently skipped** with a WARNING log, and the tool
       executes without input validation.  Install with::

           pip install jsonschema

       No runtime config flag exists for this — the log line is the signal.
       # ponytail: no config flag, log-warning is enough; add if this
       # becomes a compliance/security requirement.
    """

    def _pre(ctx: ToolContext) -> ToolContext | None:
        # Lazy imports to avoid circular dependencies
        from src.core.actions.tool_registry import tool_registry

        spec = tool_registry.get(ctx.tool_name)
        if spec is None or spec.input_schema is None:
            return ctx  # no schema — skip

        try:
            import jsonschema
        except ImportError:
            logger.warning(
                "jsonschema not installed — input schema validation disabled. "
                "Install with: pip install jsonschema"
            )
            return ctx  # jsonschema not available — skip validation

        # Strip meta-params (underscore-prefixed, e.g. _confirmed)
        # before validation — they are added by execute(), not by the
        # tool's declared schema.
        clean_params = {k: v for k, v in ctx.params.items() if not k.startswith("_")}

        try:
            jsonschema.validate(instance=clean_params, schema=spec.input_schema)
        except jsonschema.ValidationError as exc:
            ctx.blocked = True
            ctx.block_reason = f"Input validation failed: {exc.message}"
            logger.warning(
                "Tool %r input validation failed: %s",
                ctx.tool_name,
                exc.message,
            )
            return None

        return ctx

    return _pre


def _build_duplicate_pruner() -> PostHook:
    """Post-hook that replaces duplicate tool results with a one-line summary.

    Tracks MD5 hashes of recent tool results in a sliding window (20 entries).
    If the same content appears again, the result is replaced with a concise
    placeholder — saving context tokens without losing information (the first
    occurrence is already in the conversation history).
    """
    _hashes: deque[str] = deque(maxlen=20)

    async def _prune(ctx: ToolContext) -> None:
        if ctx.result is None:
            return
        try:
            content = json.dumps(ctx.result, sort_keys=True, default=str)
            content_hash = hashlib.md5(content.encode()).hexdigest()[:12]
            if content_hash in _hashes:
                ctx.result = {
                    "info": "[Duplicate tool output — same content as a previous call]"
                }
                logger.debug("Pruned duplicate result from %s", ctx.tool_name)
            else:
                _hashes.append(content_hash)
        except Exception:
            pass  # best-effort, never break tool execution

    return _prune


def init_default_middlewares(chain: MiddlewareChain | None = None) -> None:
    """Register built-in middlewares into *chain* (or the global one).

    Safe to call multiple times — second call is a no-op.
    """
    chain = chain or middleware_chain
    if chain._defaults_initialized:
        return  # already initialised

    # Input validation (priority 20 — runs early in pre-hook chain)
    chain.register(
        name="input_validator",
        priority=20,
        pre=_build_input_validator(),
    )

    # Circuit breaker (priority 30 — runs after validation, before handler)
    cb_pre, cb_post = _build_circuit_breaker()
    chain.register(
        name="circuit_breaker",
        priority=30,
        pre=cb_pre,
        post=cb_post,
    )

    # Duplicate pruner (priority 65 — runs after circuit breaker, before audit)
    chain.register(
        name="duplicate_pruner",
        priority=65,
        post=_build_duplicate_pruner(),
    )

    # Audit (priority 70 — runs after handler)
    chain.register(
        name="audit",
        priority=70,
        post=_build_tool_audit_post(),
    )

    # Metrics (priority 90 — runs last)
    chain.register(
        name="metrics",
        priority=90,
        post=_build_tool_metrics_post(),
    )

    chain._defaults_initialized = True
    logger.debug("Default middlewares registered: %s", chain.registered)
