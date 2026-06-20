"""Test ProviderFallback async context manager lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.llm.provider_fallback import ProviderFallback


@pytest.mark.asyncio
async def test_provider_fallback_closes_providers_on_aexit():
    """ProviderFallback.__aexit__ must close all child providers."""
    primary = AsyncMock()
    secondary = AsyncMock()

    async with ProviderFallback([primary, secondary]) as fallback:
        assert fallback is not None

    primary.close.assert_awaited_once()
    secondary.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_fallback_closes_on_aexit_with_exception():
    """ProviderFallback.__aexit__ must close providers even when body raises."""
    primary = AsyncMock()
    secondary = AsyncMock()

    with pytest.raises(RuntimeError, match="boom"):
        async with ProviderFallback([primary, secondary]) as fallback:
            assert fallback is not None
            raise RuntimeError("boom")

    primary.close.assert_awaited_once()
    secondary.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_fallback_skips_provider_without_close():
    """ProviderFallback.close() must not crash if a provider lacks close()."""
    primary = AsyncMock()
    del primary.close
    secondary = AsyncMock()

    async with ProviderFallback([primary, secondary]) as fallback:
        assert fallback is not None

    secondary.close.assert_awaited_once()
    # primary has no close attr, so nothing to assert
    assert not hasattr(primary, "close")
