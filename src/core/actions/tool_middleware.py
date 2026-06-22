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


from src.core.actions.circuit_breaker import ToolCircuitBreaker, CBState  # re-export for backward compat


class DecisionRepairGuard:
    """Decision-repair: ≥3 fails in ≤5 steps → stash repaired decision for next LLM turn.

    Sibling to ToolCircuitBreaker (reuses CBState-style failure counter).
    In-memory only (no DB on request path). Bounded deque prevents overflow.
    Gated by settings.reward_loop_enabled + settings.decision_repair_failure_threshold.
    """

    _lock: asyncio.Lock | None = None
    _failures: dict[str, deque] = {}  # signature → deque of timestamps
    _stash: dict[int, str] = {}  # telegram_id → stashed repair hint

    FAILURE_THRESHOLD: int = 3  # overridden by settings at runtime
    STEP_WINDOW: int = 5  # overridden by settings at runtime

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

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
        async with cls._get_lock():
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
        # ponytail: _stash write-only per key, single caller per user;
        # sync wrapper is fine since asyncio.Lock requires async ctx.

    @classmethod
    def pop_stash(cls, telegram_id: int) -> str | None:
        """Pop and clear stashed hint. No fallback — prevents cross-user data leak."""
        return cls._stash.pop(telegram_id, None)

    @classmethod
    async def cleanup_stale(cls) -> int:
        """Evict old failure entries. Called from periodic cleanup.

        Now async + lock-protected to prevent race with bump_failure()
        which mutates _failures under the same lock.
        """
        evicted = 0
        now = time.monotonic()
        async with cls._get_lock():
            for sig in list(cls._failures.keys()):
                d = cls._failures[sig]
                while d and (now - d[0]) > 300:  # 5 min TTL
                    d.popleft()
                    evicted += 1
                if not d:
                    del cls._failures[sig]
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
                logger.warning(
                    "tool_middleware: JSONL metric write failed", exc_info=True
                )

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
                        # ponytail: single-user bot, owner_telegram_id fallback is safe.
                        # Populate ctx.meta["telegram_id"] when multi-user goes live.
                        DecisionRepairGuard.stash_repair(
                            ctx.meta.get("telegram_id", settings.owner_telegram_id),
                            hint,
                        )
                except Exception:
                    logger.warning(
                        "tool_middleware: DecisionRepairGuard stash_repair failed",
                        exc_info=True,
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

       ``jsonschema`` is an optional dependency.  If not installed and a tool
       has an ``input_schema``, the tool is **blocked** with an ERROR log
       (fail-safe: no validation means no execution).  Tools without an
       ``input_schema`` are unaffected.  Install with::

           pip install jsonschema

       No runtime config flag exists for this — the log line is the signal.
       # ponytail: no config flag, log-warning is enough; add if this
       # becomes a compliance/security requirement.
    """

    async def _pre(ctx: ToolContext) -> ToolContext | None:
        # Lazy imports to avoid circular dependencies
        from src.core.actions.tool_registry import tool_registry

        spec = tool_registry.get(ctx.tool_name)
        if spec is None or spec.input_schema is None:
            return ctx  # no schema — skip

        try:
            import jsonschema
        except ImportError:
            # Fail-safe: if jsonschema is missing and tool has a schema,
            # BLOCK the tool rather than silently skipping validation.
            logger.error(
                "jsonschema not installed — tool %r blocked (input schema "
                "validation unavailable). Install with: pip install jsonschema",
                ctx.tool_name,
            )
            ctx.blocked = True
            ctx.block_reason = (
                "Input validation unavailable: jsonschema not installed. "
                "Install with: pip install jsonschema"
            )
            return None

        # Strip meta-params (underscore-prefixed, e.g. _confirmed)
        # before validation — they are added by execute(), not by the
        # tool's declared schema.
        clean_params = {k: v for k, v in ctx.params.items() if not k.startswith("_")}

        # ponytail: jsonschema.validate is CPU-bound for complex schemas.
        # Run in executor to avoid blocking event loop (was: sync call).
        def _validate() -> None:
            jsonschema.validate(instance=clean_params, schema=spec.input_schema)

        try:
            await asyncio.to_thread(_validate)
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
