"""Tests for ``src.core.infra.lazy_import``."""

from __future__ import annotations

import asyncio

import pytest

from src.core.infra.lazy_import import (
    LazyDepRegistry,
    lazy_import,
    lazy_import_or_none,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _reset_registry() -> None:
    """Purge registry so tests are isolated."""
    LazyDepRegistry.reset()


# ------------------------------------------------------------------
# Basic lazy_import — sync
# ------------------------------------------------------------------


class TestSyncLazyImport:
    """Synchronous access patterns."""

    def teardown_method(self) -> None:
        _reset_registry()

    def test_lazy_import_math_sync(self) -> None:
        """Accessing an attribute triggers sync import."""
        math_proxy = lazy_import("math")

        # Nothing loaded yet
        assert math_proxy.is_pending

        val = math_proxy.pi

        assert math_proxy.is_loaded
        assert isinstance(val, float)
        assert val == pytest.approx(3.14159, rel=1e-4)

    def test_lazy_import_os_module_level_attr(self) -> None:
        """Module-level constant access works."""
        os_proxy = lazy_import("os")
        assert os_proxy.sep in ("/", "\\")

    def test_lazy_import_sys_callable(self) -> None:
        """Callable access works."""
        sys_proxy = lazy_import("sys")
        # getrecursionlimit is a callable
        limit = sys_proxy.getrecursionlimit()
        assert isinstance(limit, int)
        assert limit > 0

    def test_lazy_import_required_missing_raises(self) -> None:
        """Non-optional import of a nonexistent module must raise."""
        proxy = lazy_import("nonexistent_module_xyz_12345", optional=False)
        with pytest.raises(ImportError):
            _ = proxy.something

    def test_lazy_import_optional_missing_is_noneish(self) -> None:
        """Optional import returns None-like proxy."""
        proxy = lazy_import("nonexistent_module_xyz_12345", optional=True)
        assert proxy.is_pending  # not loaded yet

        # Accessing any attr on unavailable optional module → AttributeError
        with pytest.raises(AttributeError):
            _ = proxy.something

        # After attempted access, module is None (not _SENTINEL)
        assert proxy._module is None  # type: ignore[attr-defined]

    def test_optional_missing_does_not_leak_import_error(self) -> None:
        """First access to optional-missing does NOT raise ImportError."""
        proxy = lazy_import("nonexistent_module_xyz_12345", optional=True)
        # Access raises AttributeError (from getattr(None, ...)), not ImportError
        with pytest.raises(AttributeError):
            _ = proxy.something

    def test_repr_pending(self) -> None:
        proxy = lazy_import("math")
        assert "(pending)" in repr(proxy)

    def test_repr_loaded(self) -> None:
        proxy = lazy_import("math")
        _ = proxy.pi
        assert repr(proxy) == repr(__import__("math"))

    def test_repr_unavailable(self) -> None:
        proxy = lazy_import("nonexistent_module_xyz_12345", optional=True)
        try:
            _ = proxy.something
        except AttributeError:
            pass
        assert "(unavailable)" in repr(proxy)

    def test_dir_eager_loads(self) -> None:
        proxy = lazy_import("math")
        assert proxy.is_pending
        names = dir(proxy)
        assert proxy.is_loaded
        assert "pi" in names

    def test_private_attr_does_not_trigger_import(self) -> None:
        """Accessing _-prefixed names should not trigger import."""
        proxy = lazy_import("nonexistent_module_xyz_12345")
        with pytest.raises(AttributeError):
            _ = proxy.__something_private
        assert proxy.is_pending  # import was NOT triggered


# ------------------------------------------------------------------
# Async pre-loading
# ------------------------------------------------------------------


class TestAsyncLazyImport:
    """Async ``_ensure_loaded`` patterns."""

    def teardown_method(self) -> None:
        _reset_registry()

    @pytest.mark.asyncio
    async def test_ensure_loaded_success(self) -> None:
        proxy = lazy_import("math")
        await proxy._ensure_loaded()
        assert proxy.is_loaded
        assert proxy.pi == pytest.approx(3.14159, rel=1e-4)

    @pytest.mark.asyncio
    async def test_ensure_loaded_required_missing(self) -> None:
        proxy = lazy_import("nonexistent_module_xyz_12345")
        with pytest.raises(ImportError):
            await proxy._ensure_loaded()
        assert proxy.is_failed

    @pytest.mark.asyncio
    async def test_ensure_loaded_optional_missing_no_raise(self) -> None:
        proxy = lazy_import("nonexistent_module_xyz_12345", optional=True)
        await proxy._ensure_loaded()  # no exception
        assert proxy._module is None  # type: ignore[attr-defined]
        assert not proxy.is_loaded
        assert not proxy.is_failed

    @pytest.mark.asyncio
    async def test_ensure_loaded_idempotent(self) -> None:
        """Multiple calls to _ensure_loaded do not re-import."""
        proxy = lazy_import("math")
        await proxy._ensure_loaded()
        await proxy._ensure_loaded()  # second call — immediate return
        assert proxy.is_loaded

    @pytest.mark.asyncio
    async def test_concurrent_ensure_loaded(self) -> None:
        """Concurrent _ensure_loaded calls only import once."""
        proxy = lazy_import("math")

        async def load() -> None:
            await proxy._ensure_loaded()

        await asyncio.gather(*(load() for _ in range(10)))
        assert proxy.is_loaded


# ------------------------------------------------------------------
# Factory helpers
# ------------------------------------------------------------------


class TestFactories:
    """``lazy_import`` / ``lazy_import_or_none`` factories."""

    def teardown_method(self) -> None:
        _reset_registry()

    def test_lazy_import_default_not_optional(self) -> None:
        proxy = lazy_import("math")
        assert proxy._optional is False  # type: ignore[attr-defined]

    def test_lazy_import_or_none_is_optional(self) -> None:
        proxy = lazy_import_or_none("math")
        assert proxy._optional is True  # type: ignore[attr-defined]

    def test_lazy_import_or_none_loads_real_module(self) -> None:
        proxy = lazy_import_or_none("math")
        val = proxy.pi
        assert isinstance(val, float)


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------


class TestLazyDepRegistry:
    """``LazyDepRegistry`` global tracking."""

    def teardown_method(self) -> None:
        _reset_registry()

    def test_register_and_get_loaded(self) -> None:
        p1 = lazy_import("math")
        lazy_import("os")  # registered but not yet loaded
        _ = p1.pi  # trigger load
        loaded = LazyDepRegistry.get_loaded()
        assert "math" in loaded
        assert "os" not in loaded  # not accessed yet

    def test_get_failed(self) -> None:
        proxy = lazy_import("nonexistent_module_xyz_12345")
        try:
            _ = proxy.something
        except ImportError:
            pass
        failed = LazyDepRegistry.get_failed()
        assert "nonexistent_module_xyz_12345" in failed

    def test_get_pending(self) -> None:
        lazy_import("math")  # registered but not touched
        pending = LazyDepRegistry.get_pending()
        assert "math" in pending

    def test_health(self) -> None:
        p1 = lazy_import("math")
        _ = p1.pi
        lazy_import("os")  # registered, pending
        h = LazyDepRegistry.health()
        assert h["total"] == 2
        assert h["loaded"] == 1
        assert h["failed"] == 0
        assert h["pending"] == 1

    def test_health_after_failure(self) -> None:
        proxy = lazy_import("nonexistent_module_xyz_12345")
        try:
            _ = proxy.something
        except ImportError:
            pass
        h = LazyDepRegistry.health()
        assert h["failed"] == 1
        assert h["pending"] == 0
        assert h["loaded"] == 0

    def test_health_with_optional_failure(self) -> None:
        proxy = lazy_import("nonexistent_module_xyz_12345", optional=True)
        try:
            _ = proxy.something
        except AttributeError:
            pass
        h = LazyDepRegistry.health()
        # Optional failure: _module=None, not _SENTINEL → not pending
        # _error is None → not failed. _module is None → not loaded.
        assert h["pending"] == 0
        assert h["loaded"] == 0
        assert h["failed"] == 0
        assert h["total"] == 1

    def test_register_dedup(self) -> None:
        """Second proxy for same name does NOT overwrite first."""
        lazy_import("math")
        lazy_import("math")
        assert LazyDepRegistry.health()["total"] == 1  # deduplicated

    def test_reset(self) -> None:
        lazy_import("math")
        LazyDepRegistry.reset()
        assert LazyDepRegistry.health()["total"] == 0


# ------------------------------------------------------------------
# Properties
# ------------------------------------------------------------------


class TestProperties:
    """``is_loaded`` / ``is_failed`` / ``is_pending`` introspection."""

    def teardown_method(self) -> None:
        _reset_registry()

    def test_is_loaded_pending_failed_after_success(self) -> None:
        proxy = lazy_import("math")
        assert proxy.is_pending
        assert not proxy.is_loaded
        assert not proxy.is_failed

        _ = proxy.pi

        assert proxy.is_loaded
        assert not proxy.is_pending
        assert not proxy.is_failed

    def test_is_failed_after_required_missing(self) -> None:
        proxy = lazy_import("nonexistent_module_xyz_12345")
        try:
            _ = proxy.something
        except ImportError:
            pass
        assert proxy.is_failed
        assert not proxy.is_loaded
        assert not proxy.is_pending

    def test_is_pending_after_optional_missing(self) -> None:
        """Optional failure sets _module=None, not _SENTINEL → not pending."""
        proxy = lazy_import("nonexistent_module_xyz_12345", optional=True)
        try:
            _ = proxy.something
        except AttributeError:
            pass
        assert not proxy.is_pending
        assert not proxy.is_loaded
        assert not proxy.is_failed


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


class TestEdgeCases:
    """Corner-case behaviour."""

    def teardown_method(self) -> None:
        _reset_registry()

    def test_import_error_cached(self) -> None:
        """Once import fails, second access does not re-attempt."""
        proxy = lazy_import("nonexistent_module_xyz_12345")
        for _ in range(3):
            with pytest.raises(ImportError):
                _ = proxy.something
        # Still exactly one failed entry
        assert LazyDepRegistry.health()["failed"] == 1

    def test_optional_import_error_not_cached_as_failure(self) -> None:
        """Optional failure sets _module=None, not _error."""
        proxy = lazy_import("nonexistent_module_xyz_12345", optional=True)
        for _ in range(3):
            with pytest.raises(AttributeError):
                _ = proxy.something
        assert not proxy.is_failed
        assert proxy._module is None  # type: ignore[attr-defined]
