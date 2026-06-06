"""Application context — single source of truth for all singletons.

Usage:
    ctx = AppContext()
    await ctx.initialize(config)
    # ... bot runs ...
    await ctx.shutdown()

In tests:
    ctx = AppContext()
    ctx.cache_manager = MockCacheManager()  # inject mock
    ctx.config = settings
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Holder for all application-wide singletons.

    All fields start as None. Call :meth:`initialize` to wire them up.
    In tests, inject mocks directly into fields.

    Each ``initialize()`` call replaces the module-level global singletons
    so that existing ``from x import y`` imports continue to work — they
    will resolve to the instance stored in this context.
    """

    # Cache layer
    cache_manager: Any = None
    pattern_cache: Any = None

    # Tools & orchestration
    tool_registry: Any = None

    # Background tasks
    task_manager: Any = None

    # Context engine (pluggable providers)
    context_engine: Any = None

    # Notification queue (batched delivery)
    notification_queue: Any = None

    # Vector store (lazy singleton via get_vector_store)
    vector_store: Any = None

    # Config reference
    config: Any = None

    # --- Lifecycle --------------------------------------------------------

    async def initialize(self, settings=None) -> None:
        """Wire up all singletons. Call once at startup.

        Args:
            settings: Application config (:class:`src.config.Settings`).
                If omitted, ``self.config`` must already be set.
        """
        if settings is not None:
            self.config = settings

        # ── Cache layer ──────────────────────────────────────────────
        # IMPORTANT: reset cache_manager BEFORE pattern_cache, because
        # pattern_cache.__init__() registers its internal ManagedCache
        # with the module-level cache_manager singleton.
        from src.core.cache.manager import (
            _reset_cache_manager_for_test,
        )

        self.cache_manager = _reset_cache_manager_for_test()

        from src.core.intelligence.pattern_cache import (
            _reset_pattern_cache_for_test,
        )

        self.pattern_cache = _reset_pattern_cache_for_test()

        # ── Decorator-populated singletons (reference only) ──────────
        # These singletons are populated at import time by @tool and
        # @task_manager.task decorators. We reference the existing
        # instances rather than creating fresh (empty) ones.
        import src.core.actions.tool_registry as _tr_mod

        self.tool_registry = _tr_mod.tool_registry

        import src.core.infra.task_manager as _tm_mod

        self.task_manager = _tm_mod.task_manager

        import src.core.context.engine as _ce_mod

        self.context_engine = _ce_mod.engine

        import src.core.scheduling.notification_queue as _nq_mod

        self.notification_queue = _nq_mod.notification_queue

        logger.info("AppContext initialized (%d singletons wired)", 7)

    async def shutdown(self) -> None:
        """Graceful shutdown — close connections, flush caches."""
        if self.cache_manager and hasattr(self.cache_manager, "reset_for_test"):
            await self.cache_manager.reset_for_test()
        logger.info("AppContext shutdown complete")


# ── Global singleton for backward compatibility ───────────────────────────

_app_context: Optional[AppContext] = None


def get_app_context() -> AppContext:
    """Get the global AppContext. Create if not exists.

    This is the preferred way to access singletons from anywhere
    in the codebase::

        ctx = get_app_context()
        await ctx.cache_manager.cleanup_all()

    For backward compatibility, existing ``from x import y`` imports
    continue to work — the module-level globals are replaced when
    :meth:`AppContext.initialize` is called.
    """
    global _app_context
    if _app_context is None:
        _app_context = AppContext()
    return _app_context


def set_app_context(ctx: AppContext) -> None:
    """Replace the global AppContext (for testing).

    Typical usage::

        ctx = AppContext()
        ctx.cache_manager = mock_cache
        set_app_context(ctx)
    """
    global _app_context
    _app_context = ctx
