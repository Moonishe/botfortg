"""Unit tests for AdaptiveTTLCache — access-count-based TTL extension.

Covers:
- Basic get/set roundtrip + missing keys
- Adaptive TTL calculation (_calculate_ttl)
- TTL growth via multiple gets, cap at max_ttl
- reset_access: zeroes count and resets TTL to base
- invalidate / clear
- stats (backend + adaptive-specific fields)
- Edge cases: base_ttl=0, max_ttl==base_ttl, empty stats
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from unittest.mock import AsyncMock, patch

import pytest

# ── Must set env BEFORE importing src to avoid Settings validation errors ──
# NOTE: conftest.py also sets defaults via setdefault, but with values that
# fail pydantic validation (e.g. BOT_TOKEN="test:token").  We must OVERWRITE
# (not setdefault) to guarantee a valid configuration.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:TEST_TOKEN_HERE_abcdefghijklmnopqrstuvwx"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"
os.environ["API_ID"] = "12345"
os.environ["API_HASH"] = "0123456789abcdef0123456789abcdef"

from src.core.cache.adaptive import AdaptiveTTLCache


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════


def make_cache(
    name: str = "test",
    base_ttl: float = 3600.0,
    max_ttl: float = 86400.0,
    growth_factor: float = 2.0,
    **kwargs,
) -> AdaptiveTTLCache:
    """Convenience factory with reasonable defaults."""
    return AdaptiveTTLCache(
        name=name,
        base_ttl=base_ttl,
        max_ttl=max_ttl,
        growth_factor=growth_factor,
        **kwargs,
    )


# ══════════════════════════════════════════════════════════════════════
# Basic get/set
# ══════════════════════════════════════════════════════════════════════


class TestBasicGetSet:
    """Basic get/set roundtrip and miss behaviour."""

    @pytest.mark.asyncio
    async def test_set_then_get_returns_value(self):
        cache = make_cache()
        await cache.set("key1", "hello")
        assert await cache.get("key1") == "hello"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self):
        cache = make_cache()
        assert await cache.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_set_overwrites_previous_value(self):
        cache = make_cache()
        await cache.set("key", "old")
        await cache.set("key", "new")
        assert await cache.get("key") == "new"

    @pytest.mark.asyncio
    async def test_set_resets_access_count_to_zero(self):
        """Overwriting a key must reset its access count."""
        cache = make_cache()
        await cache.set("key", "v")
        for _ in range(8):
            await cache.get("key")
        assert await cache.get_access_count("key") == 8

        await cache.set("key", "v2")
        assert await cache.get_access_count("key") == 0

    @pytest.mark.asyncio
    async def test_multiple_keys_independent(self):
        """Access counts for different keys are independent."""
        cache = make_cache()
        await cache.set("a", 1)
        await cache.set("b", 2)

        for _ in range(5):
            await cache.get("a")

        assert await cache.get_access_count("a") == 5
        assert await cache.get_access_count("b") == 0

    @pytest.mark.asyncio
    async def test_set_resets_ttl_to_base(self):
        """set() must store base_ttl in the adaptive TTL map."""
        cache = make_cache(base_ttl=3600.0, max_ttl=86400.0)
        await cache.set("key", "v")
        assert cache._ttl_map["key"] == 3600.0


# ══════════════════════════════════════════════════════════════════════
# Adaptive TTL  (_calculate_ttl + get-driven growth)
# ══════════════════════════════════════════════════════════════════════


class TestAdaptiveTTLCalculation:
    """Pure-function tests for ``_calculate_ttl``."""

    def test_zero_accesses_returns_base_ttl(self):
        cache = make_cache(base_ttl=60.0)
        assert cache._calculate_ttl("any") == 60.0

    def test_exactly_10_accesses_doubles_ttl(self):
        """growth_factor=2.0, accesses=10 → TTL = base * 2^(10/10) = base*2."""
        cache = make_cache(base_ttl=60.0, growth_factor=2.0)
        cache._access_counts["key"] = 10
        assert cache._calculate_ttl("key") == pytest.approx(120.0)

    def test_50_accesses_growth(self):
        """60 * 2^(50/10) = 60 * 32 = 1920."""
        cache = make_cache(base_ttl=60.0, max_ttl=99999.0, growth_factor=2.0)
        cache._access_counts["key"] = 50
        assert cache._calculate_ttl("key") == pytest.approx(1920.0)

    def test_capped_at_max_ttl(self):
        """When the formula exceeds max_ttl, return max_ttl."""
        cache = make_cache(base_ttl=60.0, max_ttl=3600.0, growth_factor=2.0)
        cache._access_counts["key"] = 100  # 60 * 2^10 = 61440 → capped
        assert cache._calculate_ttl("key") == 3600.0

    def test_custom_growth_factor(self):
        """growth_factor=1.5, accesses=10 → 60 * 1.5 = 90."""
        cache = make_cache(base_ttl=60.0, max_ttl=99999.0, growth_factor=1.5)
        cache._access_counts["key"] = 10
        assert cache._calculate_ttl("key") == pytest.approx(90.0)

    def test_growth_factor_one_means_no_growth(self):
        """growth_factor=1.0 → TTL always equals base_ttl."""
        cache = make_cache(base_ttl=60.0, max_ttl=99999.0, growth_factor=1.0)
        cache._access_counts["key"] = 1000
        assert cache._calculate_ttl("key") == pytest.approx(60.0)


class TestAdaptiveTTLIntegration:
    """Integration tests: actual get() calls drive TTL upward."""

    @pytest.mark.asyncio
    async def test_ttl_increases_on_multiple_gets(self):
        """After several gets the stored TTL (in _ttl_map) exceeds base_ttl."""
        cache = make_cache(base_ttl=3600.0, max_ttl=86400.0)
        await cache.set("hot", "value")

        # Access enough times to produce a noticeable delta
        for _ in range(30):
            await cache.get("hot")

        assert cache._ttl_map["hot"] > 3600.0

    @pytest.mark.asyncio
    async def test_ttl_capped_at_max_ttl(self):
        """Even after enormous accesses, TTL must not exceed max_ttl."""
        cache = make_cache(base_ttl=3600.0, max_ttl=7200.0, growth_factor=10.0)
        await cache.set("hot", "value")

        for _ in range(200):
            await cache.get("hot")

        assert cache._ttl_map["hot"] == 7200.0

    @pytest.mark.asyncio
    async def test_update_ttl_called_when_delta_large(self):
        """Backend.update_ttl() must be invoked when |new-old| > 1.0."""
        cache = make_cache(base_ttl=3600.0, max_ttl=86400.0, growth_factor=2.0)
        await cache.set("k", "v")

        # Pre-warm: several gets to build up access count
        for _ in range(19):
            await cache.get("k")
        # access_count now = 19  →  next get() makes it 20

        # We'll verify that update_ttl is called on the 20th get.
        # The delta between old (3600 or slightly elevated) and
        # new (3600 * 2^(20/10)=14400) is well above 1.0.
        with patch.object(
            cache._backend, "update_ttl", wraps=cache._backend.update_ttl
        ) as spy:
            await cache.get("k")
            spy.assert_called_once()
            args, _ = spy.call_args
            assert args[0] == "k"
            # new TTL should be ~14400 (capped at max_ttl if lower)
            assert args[1] == pytest.approx(min(3600.0 * (2.0**2.0), 86400.0))


# ══════════════════════════════════════════════════════════════════════
# reset_access
# ══════════════════════════════════════════════════════════════════════


class TestResetAccess:
    """``reset_access()`` zeroes count and resets TTL."""

    @pytest.mark.asyncio
    async def test_resets_count_to_zero(self):
        cache = make_cache()
        await cache.set("k", "v")
        for _ in range(7):
            await cache.get("k")
        assert await cache.get_access_count("k") == 7

        await cache.reset_access("k")
        assert await cache.get_access_count("k") == 0

    @pytest.mark.asyncio
    async def test_resets_ttl_to_base(self):
        cache = make_cache(base_ttl=3600.0, max_ttl=86400.0)
        await cache.set("k", "v")
        for _ in range(30):
            await cache.get("k")
        # TTL should be elevated
        assert cache._ttl_map["k"] > 3600.0

        await cache.reset_access("k")
        assert cache._ttl_map["k"] == 3600.0

    @pytest.mark.asyncio
    async def test_calls_backend_update_ttl(self):
        """reset_access must push the new (base) TTL to the backend."""
        cache = make_cache(base_ttl=3600.0, max_ttl=86400.0)
        await cache.set("k", "v")
        for _ in range(20):
            await cache.get("k")

        with patch.object(
            cache._backend, "update_ttl", wraps=cache._backend.update_ttl
        ) as spy:
            await cache.reset_access("k")
            spy.assert_called_once_with("k", 3600.0)

    @pytest.mark.asyncio
    async def test_nonexistent_key_no_error(self):
        cache = make_cache()
        await cache.reset_access("ghost")  # must not raise
        assert await cache.get_access_count("ghost") == 0

    @pytest.mark.asyncio
    async def test_reset_then_get_still_works(self):
        """After reset_access the value is still retrievable."""
        cache = make_cache()
        await cache.set("k", "v")
        await cache.reset_access("k")
        assert await cache.get("k") == "v"


# ══════════════════════════════════════════════════════════════════════
# invalidate / clear
# ══════════════════════════════════════════════════════════════════════


class TestInvalidateClear:
    """Key removal and full-clear operations."""

    @pytest.mark.asyncio
    async def test_invalidate_removes_key(self):
        cache = make_cache()
        await cache.set("k", "v")
        assert await cache.get("k") == "v"

        removed = await cache.invalidate("k")
        assert removed is True
        assert await cache.get("k") is None

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_returns_false(self):
        cache = make_cache()
        assert await cache.invalidate("ghost") is False

    @pytest.mark.asyncio
    async def test_invalidate_cleans_metadata(self):
        """Both _ttl_map and _access_counts must be purged."""
        cache = make_cache()
        await cache.set("k", "v")
        for _ in range(5):
            await cache.get("k")

        await cache.invalidate("k")
        assert "k" not in cache._ttl_map
        assert cache._access_counts.get("k", -1) == -1  # popped → returns -1 default

    @pytest.mark.asyncio
    async def test_clear_removes_all_keys(self):
        cache = make_cache()
        await cache.set("a", 1)
        await cache.set("b", 2)
        await cache.set("c", 3)

        await cache.clear()
        assert await cache.get("a") is None
        assert await cache.get("b") is None
        assert await cache.get("c") is None

    @pytest.mark.asyncio
    async def test_clear_empties_metadata(self):
        cache = make_cache()
        await cache.set("a", 1)
        await cache.set("b", 2)

        await cache.clear()
        assert len(cache._ttl_map) == 0
        assert len(cache._access_counts) == 0


# ══════════════════════════════════════════════════════════════════════
# stats
# ══════════════════════════════════════════════════════════════════════


class TestStats:
    """``stats()`` returns combined backend + adaptive metrics."""

    @pytest.mark.asyncio
    async def test_includes_backend_info(self):
        cache = make_cache(name="mycache", base_ttl=3600.0, max_ttl=86400.0)
        await cache.set("k", "v")
        stats = await cache.stats()

        assert stats["name"] == "mycache"
        assert stats["adaptive"] is True
        assert stats["base_ttl"] == 3600.0
        assert stats["max_ttl"] == 86400.0
        assert stats["growth_factor"] == 2.0
        assert stats["size"] >= 1

    @pytest.mark.asyncio
    async def test_ttl_distribution_after_set(self):
        """All keys have base_ttl right after set."""
        cache = make_cache(base_ttl=3600.0, max_ttl=86400.0)
        await cache.set("a", 1)
        await cache.set("b", 2)

        stats = await cache.stats()
        d = stats["ttl_distribution"]
        assert d["min"] == 3600.0
        assert d["max"] == 3600.0
        assert d["avg"] == 3600.0

    @pytest.mark.asyncio
    async def test_ttl_distribution_after_access(self):
        """Frequently accessed key should raise the max TTL."""
        cache = make_cache(base_ttl=3600.0, max_ttl=86400.0)
        await cache.set("cold", 1)
        await cache.set("hot", 2)
        for _ in range(20):
            await cache.get("hot")

        stats = await cache.stats()
        d = stats["ttl_distribution"]
        assert d["min"] == 3600.0
        assert d["max"] > 3600.0
        assert d["avg"] > 3600.0

    @pytest.mark.asyncio
    async def test_empty_cache_stats(self):
        cache = make_cache()
        stats = await cache.stats()
        assert stats["size"] == 0
        assert stats["ttl_distribution"]["min"] == 0
        assert stats["ttl_distribution"]["max"] == 0
        assert stats["ttl_distribution"]["avg"] == 0
        assert stats["access_distribution"]["keys_with_0_access"] == 0

    @pytest.mark.asyncio
    async def test_access_distribution_buckets(self):
        cache = make_cache()
        await cache.set("cold", 1)  # 0 accesses
        await cache.set("warm", 2)
        await cache.set("hot", 3)

        for _ in range(5):
            await cache.get("warm")  # 5 accesses → 1-10 bucket
        for _ in range(15):
            await cache.get("hot")  # 15 accesses → 10+ bucket

        stats = await cache.stats()
        ad = stats["access_distribution"]
        assert ad["keys_with_0_access"] >= 1
        assert ad["keys_with_1_10_access"] >= 1
        assert ad["keys_with_10_plus_access"] >= 1

    @pytest.mark.asyncio
    async def test_stats_hit_rate(self):
        """Backend hit/miss metrics are propagated."""
        cache = make_cache()
        await cache.set("k", "v")

        # At least one hit
        await cache.get("k")
        # At least one miss
        await cache.get("no_such")

        stats = await cache.stats()
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1
        assert "hit_rate" in stats


# ══════════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Boundary conditions and unusual configurations."""

    @pytest.mark.asyncio
    async def test_base_ttl_zero_key_expires_immediately(self):
        """base_ttl=0 → key immediately expired by the backend."""
        cache = make_cache(base_ttl=0.0, max_ttl=3600.0)
        await cache.set("k", "v")
        # Backend expires at time.monotonic() + 0, so it's gone by now.
        assert await cache.get("k") is None

    @pytest.mark.asyncio
    async def test_base_ttl_zero_stats_still_work(self):
        cache = make_cache(base_ttl=0.0, max_ttl=3600.0)
        stats = await cache.stats()
        assert stats["base_ttl"] == 0.0

    @pytest.mark.asyncio
    async def test_max_ttl_equals_base_ttl_no_growth(self):
        """When max==base, TTL never grows beyond base."""
        cache = make_cache(base_ttl=3600.0, max_ttl=3600.0, growth_factor=2.0)
        await cache.set("k", "v")
        for _ in range(50):
            await cache.get("k")
        assert cache._ttl_map["k"] == 3600.0

    @pytest.mark.asyncio
    async def test_max_ttl_equal_base_ttl_no_update_calls(self):
        """When max==base, update_ttl should never be called (delta=0)."""
        cache = make_cache(base_ttl=3600.0, max_ttl=3600.0)
        await cache.set("k", "v")

        with patch.object(
            cache._backend, "update_ttl", wraps=cache._backend.update_ttl
        ) as spy:
            for _ in range(30):
                await cache.get("k")
            spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_access_count_nonexistent_key(self):
        cache = make_cache()
        assert await cache.get_access_count("ghost") == 0

    @pytest.mark.asyncio
    async def test_none_value_not_distinguishable(self):
        """None values can NOT be distinguished from a missing key.

        Reason: ``AdaptiveTTLCache.get()`` returns ``None`` both when
        the backend has no key and when the stored value is ``None``.
        Because the ``if value is None: return None`` guard triggers
        *before* the access-count increment, None-valued entries also
        never have their access count increased.
        """
        cache = make_cache()
        await cache.set("k", None)
        assert await cache.get("k") is None
        # Access count stays at 0 — the None guard fires before increment
        assert await cache.get_access_count("k") == 0

    @pytest.mark.asyncio
    async def test_evict_cleanup_via_on_evict(self):
        """When the backend evicts a key, adaptive metadata is cleaned."""
        cache = AdaptiveTTLCache(
            name="small",
            base_ttl=3600.0,
            max_ttl=86400.0,
            max_size=2,
        )
        await cache.set("a", 1)
        await cache.set("b", 2)
        for _ in range(5):
            await cache.get("a")  # make it "hot"

        # Third key triggers LRU eviction of the coldest ("b")
        await cache.set("c", 3)

        # "b" should be evicted → its metadata cleaned
        assert "b" not in cache._ttl_map
        assert cache._access_counts.get("b", -1) == -1
        # "a" survived
        assert "a" in cache._ttl_map

    @pytest.mark.asyncio
    async def test_on_evict_callback_receives_key_and_value(self):
        """Custom on_evict callback must be called on LRU eviction."""
        evicted = []

        def _capture(k, v):
            evicted.append((k, v))

        cache = AdaptiveTTLCache(
            name="cb",
            base_ttl=3600.0,
            max_ttl=86400.0,
            max_size=2,
            on_evict=_capture,
        )
        await cache.set("x", "X")
        await cache.set("y", "Y")
        await cache.set("z", "Z")  # pushes out "x"

        assert len(evicted) >= 1
        assert ("x", "X") in evicted
