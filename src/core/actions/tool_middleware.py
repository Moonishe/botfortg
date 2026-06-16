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
import inspect
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

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
                return True  # Allow probe call

            # state == OPEN
            elapsed = time.monotonic() - state.opened_at
            if elapsed >= cls.COOLDOWN_SECONDS:
                state.state = "HALF_OPEN"
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
            if old_state != "CLOSED":
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

            if state.state == "HALF_OPEN":
                # Probe failed — back to OPEN
                state.state = "OPEN"
                state.opened_at = time.monotonic()
                logger.warning(
                    "Circuit for %r: HALF_OPEN probe failed — back to OPEN "
                    "(failures=%d)",
                    tool_name,
                    state.failures,
                )
            elif state.failures >= cls.FAILURE_THRESHOLD and state.state != "OPEN":
                state.state = "OPEN"
                state.opened_at = time.monotonic()
                logger.warning(
                    "Circuit for %r: tripped OPEN after %d failures",
                    tool_name,
                    state.failures,
                )

    @classmethod
    async def reset(cls, tool_name: str | None = None) -> None:
        """Reset circuit state for *tool_name* (or all tools if None).

        Useful for testing.
        """
        async with cls._lock:
            if tool_name is None:
                cls._states.clear()
            else:
                cls._states.pop(tool_name, None)


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
