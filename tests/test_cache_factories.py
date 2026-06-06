"""Unit tests for cache factory functions and reset_for_test().

Covers:
  - create_cache_manager() returns a fresh CacheManager instance
  - _reset_cache_manager_for_test() swaps the global singleton
  - CacheManager.reset_for_test() clears caches
  - create_pattern_cache() returns a fresh PatternCache instance
  - _reset_pattern_cache_for_test() swaps the global singleton
  - PatternCache.reset_for_test() clears state
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

# ── Environment setup BEFORE importing src modules ──────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.core.cache.manager import (
    CacheManager,
    ManagedCache,
    cache_manager,
    create_cache_manager,
    _reset_cache_manager_for_test,
)
from src.core.intelligence.pattern_cache import (
    PatternCache,
    pattern_cache,
    create_pattern_cache,
    _reset_pattern_cache_for_test,
)


# ═══════════════════════════════════════════════════════════════════
#  Tests: CacheManager factory / reset
# ═══════════════════════════════════════════════════════════════════


class TestCacheManagerFactory:
    """Tests for CacheManager factory functions."""

    def test_create_cache_manager_returns_fresh_instance(self):
        cm = create_cache_manager()
        assert isinstance(cm, CacheManager)
        assert cm._caches == {}

    @pytest.mark.asyncio
    async def test_reset_cache_manager_for_test_swaps_singleton(self):
        # Save the original
        original = cache_manager
        new_cm = _reset_cache_manager_for_test()

        # New singleton
        import src.core.cache.manager as _mod

        assert _mod.cache_manager is new_cm
        assert _mod.cache_manager is not original
        assert isinstance(new_cm, CacheManager)
        assert new_cm._caches == {}

        # Restore original
        _mod.cache_manager = original

    @pytest.mark.asyncio
    async def test_reset_for_test_clears_state(self):
        # Create a fresh manager, register a cache, then reset
        cm = CacheManager()
        cache = ManagedCache(name="test_reset", max_size=100, default_ttl=60.0)
        cm.register(cache)
        await cache.set("key", "value")

        assert await cache.size() == 1

        await cm.reset_for_test()
        assert await cache.size() == 0
        assert cm._caches == {}


class TestCacheManagerResetForTest:
    """Additional tests for reset_for_test() behaviour."""

    @pytest.mark.asyncio
    async def test_reset_for_test_stops_background_cleanup(self):
        cm = CacheManager()
        await cm.start_background_cleanup(interval=0.1)
        assert cm._cleanup_task is not None
        assert not cm._cleanup_task.done()

        await cm.reset_for_test()
        assert cm._cleanup_task is None or cm._cleanup_task.done()

    @pytest.mark.asyncio
    async def test_reset_for_test_multiple_caches(self):
        cm = CacheManager()
        c1 = ManagedCache(name="c1", max_size=10)
        c2 = ManagedCache(name="c2", max_size=10)
        cm.register(c1)
        cm.register(c2)
        await c1.set("a", 1)
        await c2.set("b", 2)

        await cm.reset_for_test()
        assert cm._caches == {}
        assert await c1.size() == 0
        assert await c2.size() == 0


# ═══════════════════════════════════════════════════════════════════
#  Tests: PatternCache factory / reset
# ═══════════════════════════════════════════════════════════════════


class TestPatternCacheFactory:
    """Tests for PatternCache factory functions."""

    def test_create_pattern_cache_returns_fresh_instance(self):
        pc = create_pattern_cache()
        assert isinstance(pc, PatternCache)
        assert pc._hits == 0
        assert pc._misses == 0
        assert pc._bypasses == 0

    def test_reset_pattern_cache_for_test_swaps_singleton(self):
        original = pattern_cache
        new_pc = _reset_pattern_cache_for_test()

        import src.core.intelligence.pattern_cache as _mod

        assert _mod.pattern_cache is new_pc
        assert _mod.pattern_cache is not original
        assert isinstance(new_pc, PatternCache)
        assert new_pc._hits == 0

        # Restore original
        _mod.pattern_cache = original

    @pytest.mark.asyncio
    async def test_pattern_cache_reset_for_test_clears_state(self):
        pc = PatternCache()
        await pc.record_pattern(1, "intent", "action", ttl=3600)
        stats_before = await pc.stats()
        assert stats_before["entries"] >= 1

        await pc.reset_for_test()
        stats_after = await pc.stats()
        assert stats_after["entries"] == 0
        assert pc._hits == 0
        assert pc._misses == 0

    @pytest.mark.asyncio
    async def test_pattern_cache_reset_for_test_clears_bypasses(self):
        pc = PatternCache()
        pc._bypasses = 42
        await pc.reset_for_test()
        assert pc._bypasses == 0
