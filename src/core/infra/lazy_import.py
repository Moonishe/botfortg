"""Lazy dependency import — proxy-based deferred module loading.

Provides ``LazyModule``, a transparent proxy that postpones ``importlib.import_module``
until the first attribute access.  Supports optional dependencies (graceful ``None``
on ``ImportError``), async-aware loading with ``asyncio.Lock``, and a global registry
for health-check auditing.

Usage::

    from src.core.infra.lazy_import import lazy_import, LazyDepRegistry

    telethon = lazy_import("telethon")
    qrcode   = lazy_import_or_none("qrcode")

    # In async context — pre-load explicitly (optional but recommended):
    await telethon._ensure_loaded()

    # In sync context — first attribute access triggers import:
    client = telethon.TelegramClient(...)

    # Audit:
    print(LazyDepRegistry.health())
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_LOCK_WARN_INTERVAL = 5.0  # seconds between warnings about slow imports
_SENTINEL = object()


class LazyModule:
    """Proxy that defers ``importlib.import_module`` to first attribute access.

    The real module is loaded **synchronously** inside ``__getattr__`` so that
    lazy proxies work transparently in synchronous code paths.  For async-aware
    pre-loading (which avoids blocking the event loop for heavy modules), call
    ``await proxy._ensure_loaded()`` before accessing attributes.
    """

    __slots__ = ("_error", "_lock", "_module", "_module_name", "_optional")

    # Type annotations for pyright — purely declarative (no class attributes created).
    _module_name: str
    _optional: bool
    _module: Any
    _lock: asyncio.Lock
    _error: ImportError | None

    def __init__(self, module_name: str, *, optional: bool = False) -> None:
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_optional", optional)
        object.__setattr__(self, "_module", _SENTINEL)
        object.__setattr__(self, "_lock", asyncio.Lock())
        object.__setattr__(self, "_error", None)
        LazyDepRegistry.register(self)

    # ------------------------------------------------------------------
    # Async pre-loading
    # ------------------------------------------------------------------

    async def _ensure_loaded(self) -> None:
        """Pre-load the real module asynchronously (non-blocking).

        Uses a double-checked ``asyncio.Lock`` so concurrent callers do not
        trigger duplicate imports.  If the module is optional and unavailable,
        ``_module`` is set to ``None`` (no exception).  For non-optional
        modules a failing import raises ``ImportError``.
        """
        if self._module is not _SENTINEL or self._error is not None:
            return

        async with self._lock:
            if self._module is not _SENTINEL or self._error is not None:
                return

            started = time.monotonic()
            try:
                mod = importlib.import_module(self._module_name)
                object.__setattr__(self, "_module", mod)
                elapsed = time.monotonic() - started
                logger.debug(
                    "LazyImport: loaded %s in %.2fs",
                    self._module_name,
                    elapsed,
                )
            except ImportError as exc:
                object.__setattr__(self, "_module", None)
                if self._optional:
                    logger.debug(
                        "LazyImport: optional %s not available",
                        self._module_name,
                    )
                else:
                    object.__setattr__(self, "_error", exc)
                    logger.exception(
                        "LazyImport: failed to load required module %s",
                        self._module_name,
                    )
                    raise

    # ------------------------------------------------------------------
    # Sync fallback — __getattr__
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Trigger synchronous import on first attribute access."""
        # Avoid recursion when pickle/copy introspects __getstate__ etc.
        if name.startswith("_"):
            raise AttributeError(name)

        # If import already failed permanently, re-raise the cached error.
        if self._error is not None:
            raise self._error

        if self._module is _SENTINEL:
            self._sync_import()

        return getattr(self._module, name)

    def __dir__(self) -> list[str]:
        """Support tab-completion / ``dir()`` by eagerly loading."""
        if self._module is _SENTINEL:
            self._sync_import()
        return dir(self._module) if self._module is not None else []

    def __repr__(self) -> str:
        if self._module is _SENTINEL:
            return f"<LazyModule '{self._module_name}' (pending)>"
        if self._module is None:
            return f"<LazyModule '{self._module_name}' (unavailable)>"
        return repr(self._module)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sync_import(self) -> None:
        """Perform the import synchronously (callable from any context)."""
        if self._module is not _SENTINEL or self._error is not None:
            return

        try:
            mod = importlib.import_module(self._module_name)
            object.__setattr__(self, "_module", mod)
        except ImportError as exc:
            object.__setattr__(self, "_module", None)
            if not self._optional:
                object.__setattr__(self, "_error", exc)
                raise

    @property
    def is_loaded(self) -> bool:
        """``True`` once the real module has been successfully imported."""
        return self._module is not _SENTINEL and self._module is not None

    @property
    def is_failed(self) -> bool:
        """``True`` if a *required* import failed permanently."""
        return self._error is not None

    @property
    def is_pending(self) -> bool:
        """``True`` if the module has not been loaded yet."""
        return self._module is _SENTINEL


# ------------------------------------------------------------------
# Factory helpers
# ------------------------------------------------------------------


def lazy_import(
    module_name: str,
    *,
    optional: bool = False,
) -> LazyModule:
    """Create a lazy module proxy.

    Args:
        module_name: Full module name for ``importlib.import_module()``
            (e.g. ``"telethon"``, ``"PIL.Image"``).
        optional: If ``True``, sets ``_module = None`` on ``ImportError``
            instead of raising.  Attribute access on an unavailable optional
            module raises ``AttributeError``.

    Returns:
        ``LazyModule`` that delegates ``__getattr__`` to the real module
        on first access.
    """
    return LazyModule(module_name, optional=optional)


def lazy_import_or_none(module_name: str) -> LazyModule:
    """Shorthand for ``lazy_import(name, optional=True)``."""
    return LazyModule(module_name, optional=True)


# ------------------------------------------------------------------
# Global registry
# ------------------------------------------------------------------


class LazyDepRegistry:
    """Collects every ``LazyModule`` instance for health-check auditing.

    Use the class methods to interrogate which dependencies have been loaded,
    which have failed, and how many are still pending.
    """

    _instances: dict[str, LazyModule] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    @classmethod
    def register(cls, proxy: LazyModule) -> None:
        """Register *proxy* under its module name (first-write-wins)."""
        # setdefault keeps the *first* instance for each name
        cls._instances.setdefault(
            proxy._module_name,  # type: ignore[attr-defined]
            proxy,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @classmethod
    def get_loaded(cls) -> list[str]:
        """Names of modules that have been successfully imported."""
        return [
            name
            for name, p in cls._instances.items()
            if p._module is not _SENTINEL and p._module is not None  # type: ignore[attr-defined]
        ]

    @classmethod
    def get_failed(cls) -> list[str]:
        """Names of *required* modules whose import failed permanently."""
        return [
            name
            for name, p in cls._instances.items()
            if p._error is not None  # type: ignore[attr-defined]
        ]

    @classmethod
    def get_pending(cls) -> list[str]:
        """Names of modules that have never been loaded."""
        return [
            name
            for name, p in cls._instances.items()
            if p._module is _SENTINEL  # type: ignore[attr-defined]
        ]

    @classmethod
    def health(cls) -> dict[str, int]:
        """Return a summary dict: ``{total, loaded, failed, pending}``."""
        loaded = len(cls.get_loaded())
        failed = len(cls.get_failed())
        pending = len(cls.get_pending())
        total = len(cls._instances)
        return {
            "total": total,
            "loaded": loaded,
            "failed": failed,
            "pending": pending,
        }

    @classmethod
    def reset(cls) -> None:
        """Clear the registry (mostly useful in tests)."""
        cls._instances.clear()
