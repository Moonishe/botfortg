"""Tool Middleware Chain — pre/post hooks for tool execution.

A lightweight pipeline: sync pre-hooks → execute → async post-hooks.
Each middleware can inspect/block/modify context.

Usage::

    from src.core.actions.tool_middleware import middleware_chain

    middleware_chain.register(
        name="metrics", priority=90,
        post=lambda ctx: _record_latency(ctx),
    )
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    tool_name: str
    params: dict[str, Any]
    result: dict[str, Any] | None = None
    blocked: bool = False
    block_reason: str = ""
    started_at: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


PreHook = Callable[[ToolContext], ToolContext | None]
PostHook = Callable[[ToolContext], Coroutine[Any, Any, None]]


class MiddlewareChain:
    def __init__(self) -> None:
        self._mw: list[tuple[str, int, PreHook | None, PostHook | None]] = []

    def register(
        self,
        *,
        name: str,
        priority: int = 50,
        pre: PreHook | None = None,
        post: PostHook | None = None,
    ) -> None:
        self._mw.append((name, priority, pre, post))
        self._mw.sort(key=lambda x: x[1])

    async def wrap(
        self,
        tool_name: str,
        params: dict[str, Any],
        execute_fn: Callable[..., Coroutine[Any, Any, dict]],
    ) -> dict[str, Any]:
        ctx = ToolContext(
            tool_name=tool_name, params=params, started_at=time.monotonic()
        )

        for _name, _, pre, _ in self._mw:
            if pre is None:
                continue
            try:
                new_ctx = pre(ctx)
            except Exception:
                logger.exception("Pre-hook %r failed", _name)
                continue
            if new_ctx is None:
                return {"error": f"Blocked by '{_name}'", "blocked_by": "middleware"}
            ctx = new_ctx

        try:
            ctx.result = await execute_fn(tool_name, ctx.params)
        except Exception as exc:
            ctx.result = {"error": str(exc)}

        for _name, _, _, post in self._mw:
            if post is None:
                continue
            try:
                await post(ctx)
            except Exception:
                logger.exception("Post-hook %r failed", _name)

        return ctx.result or {"ok": True}


middleware_chain = MiddlewareChain()
