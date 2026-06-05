"""Comprehensive unit tests for ManagedCache (TTL+LRU async cache with metrics).

Tests cover:
- Basic get/set roundtrip
- TTL expiry (get, get_metadata, cleanup_expired, update_ttl)
- LRU eviction (capacity enforcement, get/set/update_ttl promotion)
- upsert() atomic read-modify-write with double-checked locking
- invalidate() and clear()
- Metrics: hits, misses, evictions, expirations, hit_rate
- on_evict callback behaviour and error resilience
- Edge cases: max_size=1, default_ttl=0, empty cache, None values
- Concurrent upsert consistency
"""

import asyncio
import os
import time

import pytest

# Env vars MUST be set before importing from src (Settings() validates BOT_TOKEN).
# Use direct assignment (not setdefault) because conftest.py may have already
# set an invalid placeholder that must be overridden.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:TEST_TOKEN_HERE_abcdefghijklmnopqrstuvwx"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.core.cache.manager import CacheMetrics, ManagedCache  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────


class FakeClock:
    """Controllable monotonic clock for deterministic TTL/LRU tests.

    Start at 1000.0 to avoid zero-value edge cases (e.g. ``expires_at > 0``
    guards in ``get_metadata``).
    """

    def __init__(self, start: float = 1000.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        self._now += seconds
        return self._now


@pytest.fixture
def clock():
    """Return a fresh FakeClock instance for each test."""
    return FakeClock()


# ── CacheMetrics standalone tests ───────────────────────────────────────────


class TestCacheMetrics:
    """Unit tests for the CacheMetrics dataclass and its hit_rate property."""

    def test_defaults_are_zero(self):
        m = CacheMetrics()
        assert m.hits == 0
        assert m.misses == 0
        assert m.evictions == 0
        assert m.expirations == 0

    def test_hit_rate_zero_when_no_requests(self):
        m = CacheMetrics()
        assert m.hit_rate == 0.0

    def test_hit_rate_perfect(self):
        m = CacheMetrics(hits=10, misses=0)
        assert m.hit_rate == 1.0

    def test_hit_rate_half(self):
        m = CacheMetrics(hits=5, misses=5)
        assert m.hit_rate == 0.5

    def test_hit_rate_zero_on_all_misses(self):
        m = CacheMetrics(hits=0, misses=100)
        assert m.hit_rate == 0.0

    def test_hit_rate_all_hits(self):
        m = CacheMetrics(hits=50, misses=0)
        assert m.hit_rate == 1.0

    def test_hit_rate_mixed(self):
        m = CacheMetrics(hits=7, misses=3)
        assert m.hit_rate == 0.7


# ── Constructor / Init tests ────────────────────────────────────────────────


class TestManagedCacheInit:
    """Constructor validation."""

    def test_valid_constructor(self):
        c = ManagedCache(name="test", max_size=10, default_ttl=60.0)
        assert c.name == "test"
        assert c.max_size == 10
        assert c.default_ttl == 60.0
        assert c.on_evict is None
        assert c.metrics.hits == 0

    def test_max_size_must_be_positive(self):
        with pytest.raises(ValueError, match="max_size must be >= 1"):
            ManagedCache(name="test", max_size=0)
        with pytest.raises(ValueError, match="max_size must be >= 1"):
            ManagedCache(name="test", max_size=-1)

    def test_default_values(self):
        c = ManagedCache(name="t")
        assert c.max_size == 1000
        assert c.default_ttl == 300.0


# ── Basic get/set roundtrip ─────────────────────────────────────────────────


class TestBasicGetSet:
    """Fundamental get/set operations."""

    @pytest.mark.asyncio
    async def test_set_then_get_returns_value(self):
        c = ManagedCache(name="test")
        await c.set("key1", "value1")
        result = await c.get("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self):
        c = ManagedCache(name="test")
        result = await c.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_overwrites_existing(self):
        c = ManagedCache(name="test")
        await c.set("key1", "old_value")
        await c.set("key1", "new_value")
        result = await c.get("key1")
        assert result == "new_value"

    @pytest.mark.asyncio
    async def test_size_reflects_items(self):
        c = ManagedCache(name="test")
        assert await c.size() == 0
        await c.set("a", 1)
        await c.set("b", 2)
        assert await c.size() == 2
        # Overwrite — size unchanged
        await c.set("b", 3)
        assert await c.size() == 2


# ── TTL Expiry ──────────────────────────────────────────────────────────────


class TestTTLExpiry:
    """Time-to-live: get() returns None for expired, get_metadata, cleanup."""

    @pytest.mark.asyncio
    async def test_get_returns_none_for_expired_key(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=10.0)
        await c.set("key", "value")

        # Still fresh after 5 s
        clock.advance(5.0)
        assert await c.get("key") == "value"

        # Expired after 11 s total (>10 s TTL)
        clock.advance(6.0)
        assert await c.get("key") is None

    @pytest.mark.asyncio
    async def test_get_metadata_shows_expires_at(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=30.0)
        await c.set("key", "value")

        meta = await c.get_metadata("key")
        assert meta is not None
        assert meta["expires_at"] == clock() + 30.0  # expires_at was set at t=1000
        # Actually: set at clock()=1000, expires_at=1030
        assert meta["expires_at"] == 1030.0
        assert meta["ttl"] == pytest.approx(30.0)

        clock.advance(10.0)
        meta = await c.get_metadata("key")
        assert meta is not None
        assert meta["ttl"] == pytest.approx(20.0)

    @pytest.mark.asyncio
    async def test_get_metadata_returns_none_for_expired(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=5.0)
        await c.set("key", "value")
        clock.advance(10.0)

        assert await c.get_metadata("key") is None

    @pytest.mark.asyncio
    async def test_get_metadata_returns_none_for_missing(self):
        c = ManagedCache(name="test")
        assert await c.get_metadata("missing") is None

    @pytest.mark.asyncio
    async def test_cleanup_expired_removes_only_expired(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=10.0)
        await c.set("a", 1)  # t=1000,  expires_at=1010
        clock.advance(3.0)  # t=1003
        await c.set("b", 2)  # t=1003,  expires_at=1013
        clock.advance(5.0)  # t=1008
        await c.set("c", 3)  # t=1008,  expires_at=1018
        clock.advance(5.0)  # t=1013

        # a: 1010 <= 1013 → expired ✓
        # b: 1013 <= 1013 → expired ✓ (equality counts)
        # c: 1018 >  1013 → alive
        removed = await c.cleanup_expired()
        assert removed == 2
        assert await c.get("a") is None
        assert await c.get("b") is None
        assert await c.get("c") == 3
        assert await c.size() == 1

    @pytest.mark.asyncio
    async def test_cleanup_empty_cache_returns_zero(self):
        c = ManagedCache(name="test")
        assert await c.cleanup_expired() == 0

    @pytest.mark.asyncio
    async def test_update_ttl_extends_expiry(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=10.0)
        await c.set("key", "value")

        assert await c.update_ttl("key", 50.0) is True

        # Advance past original TTL (10 s) — still alive with new 50 s TTL
        clock.advance(15.0)
        assert await c.get("key") == "value"

        # Advance past new TTL
        clock.advance(40.0)  # total 55 s > 50 s new TTL
        assert await c.get("key") is None

    @pytest.mark.asyncio
    async def test_update_ttl_returns_false_for_missing(self):
        c = ManagedCache(name="test")
        assert await c.update_ttl("missing", 10.0) is False

    @pytest.mark.asyncio
    async def test_update_ttl_promotes_key_lru(self, monkeypatch, clock):
        """update_ttl must move the key to the end (LRU promotion)."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", max_size=3, default_ttl=100)
        await c.set("a", 1)
        await c.set("b", 2)
        await c.set("c", 3)

        # Promote "a" via update_ttl
        await c.update_ttl("a", 200)

        # Eviction: "b" should go (LRU), not "a"
        await c.set("d", 4)

        assert await c.get("a") == 1  # survived promotion
        assert await c.get("b") is None  # evicted
        assert await c.get("c") == 3
        assert await c.get("d") == 4

    @pytest.mark.asyncio
    async def test_default_ttl_zero_expires_immediately(self, monkeypatch, clock):
        """TTL=0 means the key is expired the moment it is set."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=0.0)
        await c.set("key", "value")

        # Immediately expired (expires_at == set time, monotonic >= expires_at)
        assert await c.get("key") is None


# ── LRU Eviction ────────────────────────────────────────────────────────────


class TestLRUEviction:
    """Least-Recently-Used eviction when cache reaches max_size."""

    @pytest.mark.asyncio
    async def test_evicts_lru_when_full(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        evicted: list = []

        def on_evict(k, v):
            evicted.append((k, v))

        c = ManagedCache(name="test", max_size=3, default_ttl=100, on_evict=on_evict)
        await c.set("a", 1)
        await c.set("b", 2)
        await c.set("c", 3)
        assert len(evicted) == 0

        # 4th item → evicts "a" (least recently used)
        await c.set("d", 4)
        assert len(evicted) == 1
        assert evicted[0] == ("a", 1)
        assert await c.get("a") is None
        assert await c.get("b") == 2
        assert await c.get("c") == 3
        assert await c.get("d") == 4

    @pytest.mark.asyncio
    async def test_get_promotes_key(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", max_size=3, default_ttl=100)
        await c.set("a", 1)
        await c.set("b", 2)
        await c.set("c", 3)

        # Access "a" → moves to end (most-recently used)
        await c.get("a")

        # "b" is now LRU
        await c.set("d", 4)
        assert await c.get("a") == 1  # survived
        assert await c.get("b") is None  # evicted
        assert await c.get("c") == 3
        assert await c.get("d") == 4

    @pytest.mark.asyncio
    async def test_set_promotes_existing_key(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", max_size=3, default_ttl=100)
        await c.set("a", 1)
        await c.set("b", 2)
        await c.set("c", 3)

        # Re-set "a" → moves to end
        await c.set("a", 100)

        await c.set("d", 4)
        assert await c.get("a") == 100  # survived
        assert await c.get("b") is None  # evicted
        assert await c.get("c") == 3

    @pytest.mark.asyncio
    async def test_max_size_one(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", max_size=1, default_ttl=100)
        await c.set("a", 1)
        assert await c.get("a") == 1

        await c.set("b", 2)
        assert await c.get("a") is None
        assert await c.get("b") == 2

    @pytest.mark.asyncio
    async def test_multiple_evictions_on_repeated_sets(self, monkeypatch, clock):
        """Every set() that triggers capacity should evict exactly one item."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", max_size=1, default_ttl=100)
        await c.set("a", 1)
        for key in ["b", "c", "d", "e"]:
            await c.set(key, key)

        assert c.metrics.evictions == 4  # one per set after the first


# ── upsert (atomic read-modify-write) ───────────────────────────────────────


class TestUpsert:
    """Tests for ``upsert()`` — the double-checked-locking read-modify-write."""

    @pytest.mark.asyncio
    async def test_creates_new_on_miss(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=100)
        factory_called = False

        def factory():
            nonlocal factory_called
            factory_called = True
            return "fresh_value"

        value, was_created = await c.upsert("key", None, factory)
        assert value == "fresh_value"
        assert was_created is True
        assert factory_called is True
        assert await c.get("key") == "fresh_value"

    @pytest.mark.asyncio
    async def test_returns_existing_on_hit(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=100)
        await c.set("key", "cached_value")

        factory_called = False

        def factory():
            nonlocal factory_called
            factory_called = True
            return "should_not_use"

        value, was_created = await c.upsert("key", None, factory)
        assert value == "cached_value"
        assert was_created is False
        assert factory_called is False  # factory skipped on hit

    @pytest.mark.asyncio
    async def test_handles_expired_key_calls_factory(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=10.0)
        await c.set("key", "old_value")
        clock.advance(15.0)  # expired

        factory_called = False

        def factory():
            nonlocal factory_called
            factory_called = True
            return "new_value"

        value, was_created = await c.upsert("key", None, factory)
        assert value == "new_value"
        assert was_created is True
        assert factory_called is True
        assert await c.get("key") == "new_value"

    @pytest.mark.asyncio
    async def test_factory_raises_on_expired_no_corruption(self, monkeypatch, clock):
        """If factory raises on an expired entry, exception propagates
        but the cache stays consistent (old entry still present, expired)."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=10.0)
        await c.set("key", "original")
        clock.advance(15.0)  # Key is now expired → factory WILL be called

        def factory():
            raise RuntimeError("factory failed")

        with pytest.raises(RuntimeError, match="factory failed"):
            await c.upsert("key", None, factory)

        # The old (expired) entry is still in the cache — not corrupted.
        # A subsequent upsert with a working factory should succeed.
        value, was_created = await c.upsert("key", None, lambda: "recovered")
        assert value == "recovered"
        assert was_created is True

    @pytest.mark.asyncio
    async def test_factory_raises_on_miss_no_side_effects(self, monkeypatch, clock):
        """Factory exception on a cache miss should not store anything."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=100)

        def factory():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await c.upsert("new_key", None, factory)

        assert await c.get("new_key") is None
        assert await c.size() == 0

    @pytest.mark.asyncio
    async def test_promotes_key_on_hit(self, monkeypatch, clock):
        """upsert() hit must promote the key (LRU)."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", max_size=3, default_ttl=100)
        await c.set("a", 1)
        await c.set("b", 2)
        await c.set("c", 3)

        value, was_created = await c.upsert("a", None, lambda: "unused")
        assert value == 1
        assert was_created is False

        await c.set("d", 4)
        assert await c.get("a") == 1  # survived
        assert await c.get("b") is None  # evicted
        assert await c.get("c") == 3

    @pytest.mark.asyncio
    async def test_evicts_lru_when_full(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        evicted: list = []
        c = ManagedCache(
            name="test",
            max_size=2,
            default_ttl=100,
            on_evict=lambda k, v: evicted.append((k, v)),
        )
        await c.set("a", 1)
        await c.set("b", 2)

        value, was_created = await c.upsert("c", None, lambda: 3)
        assert value == 3
        assert was_created is True
        assert len(evicted) == 1
        assert evicted[0] == ("a", 1)

    @pytest.mark.asyncio
    async def test_concurrent_upsert_consistent_result(self, monkeypatch, clock):
        """Two coroutines upsert the same missing key concurrently.

        The write-back double-check guarantees only one result is stored
        and both callers receive the same value.
        """
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=100)
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return f"value_{call_count}"

        (val1, created1), (val2, created2) = await asyncio.gather(
            c.upsert("shared_key", None, factory),
            c.upsert("shared_key", None, factory),
        )

        # Both must return the same cached value (the one that won the race)
        assert val1 == val2
        # At least one reports was_created=True
        assert created1 or created2
        # Cache contains exactly one entry
        assert await c.size() == 1
        # Factory may be called 1 or 2 times (race window); both are acceptable

    @pytest.mark.asyncio
    async def test_upsert_with_custom_ttl(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=100)
        value, created = await c.upsert("key", 5.0, lambda: "short_lived")
        assert created is True

        clock.advance(3.0)
        assert await c.get("key") == "short_lived"

        clock.advance(3.0)  # total 6 s > 5 s TTL
        assert await c.get("key") is None

    @pytest.mark.asyncio
    async def test_upsert_with_async_factory(self, monkeypatch, clock):
        """upsert() must support async factory callables (detected via iscoroutine)."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=100)

        async def async_factory():
            return "async_result"

        value, was_created = await c.upsert("key", None, async_factory)
        assert value == "async_result"
        assert was_created is True


# ── invalidate ──────────────────────────────────────────────────────────────


class TestInvalidate:
    """Manual key removal via invalidate()."""

    @pytest.mark.asyncio
    async def test_invalidate_existing_returns_true(self):
        c = ManagedCache(name="test")
        await c.set("key", "value")
        assert await c.invalidate("key") is True
        assert await c.get("key") is None
        assert await c.size() == 0

    @pytest.mark.asyncio
    async def test_invalidate_missing_returns_false(self):
        c = ManagedCache(name="test")
        assert await c.invalidate("missing") is False

    @pytest.mark.asyncio
    async def test_invalidate_calls_on_evict(self):
        evicted: list = []
        c = ManagedCache(name="test", on_evict=lambda k, v: evicted.append((k, v)))
        await c.set("key", "value")
        await c.invalidate("key")
        assert evicted == [("key", "value")]

    @pytest.mark.asyncio
    async def test_invalidate_does_not_increment_expirations(self, monkeypatch, clock):
        """invalidate() passes expired=False → expirations counter unchanged."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=100)
        await c.set("key", "value")
        await c.invalidate("key")
        assert c.metrics.expirations == 0


# ── clear ───────────────────────────────────────────────────────────────────


class TestClear:
    """Full cache clear."""

    @pytest.mark.asyncio
    async def test_clear_empties_cache(self):
        c = ManagedCache(name="test")
        await c.set("a", 1)
        await c.set("b", 2)
        await c.set("c", 3)

        count = await c.clear()
        assert count == 3
        assert await c.size() == 0
        assert await c.get("a") is None
        assert await c.get("b") is None

    @pytest.mark.asyncio
    async def test_clear_calls_on_evict_for_every_item(self):
        evicted: list = []
        c = ManagedCache(name="test", on_evict=lambda k, v: evicted.append((k, v)))
        await c.set("a", 1)
        await c.set("b", 2)

        await c.clear()
        assert set(evicted) == {("a", 1), ("b", 2)}

    @pytest.mark.asyncio
    async def test_clear_empty_cache_returns_zero(self):
        c = ManagedCache(name="test")
        assert await c.clear() == 0

    @pytest.mark.asyncio
    async def test_clear_handles_on_evict_exception(self):
        """A failing on_evict callback must not prevent clearing other items."""
        evicted: list = []

        def faulty_evict(k, v):
            if k == "b":
                raise RuntimeError("evict failed")
            evicted.append((k, v))

        c = ManagedCache(name="test", on_evict=faulty_evict)
        await c.set("a", 1)
        await c.set("b", 2)
        await c.set("c", 3)

        count = await c.clear()
        assert count == 3
        assert await c.size() == 0
        assert ("a", 1) in evicted
        assert ("c", 3) in evicted
        # "b" failed but cache was still cleared


# ── Metrics ─────────────────────────────────────────────────────────────────


class TestMetrics:
    """Hit/miss/eviction/expiration counters."""

    @pytest.mark.asyncio
    async def test_hits_and_misses_counted(self):
        c = ManagedCache(name="test")
        await c.set("key", "value")

        await c.get("key")  # hit
        await c.get("key")  # hit
        await c.get("missing")  # miss

        assert c.metrics.hits == 2
        assert c.metrics.misses == 1

    @pytest.mark.asyncio
    async def test_evictions_counted(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", max_size=2, default_ttl=100)
        await c.set("a", 1)
        await c.set("b", 2)
        await c.set("c", 3)  # evicts "a"
        await c.set("d", 4)  # evicts "b"

        assert c.metrics.evictions == 2

    @pytest.mark.asyncio
    async def test_expirations_counted_on_get(self, monkeypatch, clock):
        """get() on an expired key counts 1 expiration + 1 miss."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=10.0)
        await c.set("key", "value")
        clock.advance(15.0)

        await c.get("key")
        assert c.metrics.expirations == 1
        assert c.metrics.misses == 1  # expired get counts as miss

    @pytest.mark.asyncio
    async def test_expirations_counted_on_cleanup(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=10.0)
        await c.set("a", 1)
        await c.set("b", 2)
        clock.advance(15.0)

        await c.cleanup_expired()
        assert c.metrics.expirations == 2

    @pytest.mark.asyncio
    async def test_expirations_not_counted_on_invalidate(self, monkeypatch, clock):
        """invalidate() is a manual removal, not a natural expiration."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=100)
        await c.set("key", "value")
        await c.invalidate("key")

        assert c.metrics.expirations == 0

    @pytest.mark.asyncio
    async def test_stats_format(self):
        c = ManagedCache(name="my_cache", max_size=10)
        await c.set("k", "v")
        await c.get("k")  # hit
        await c.get("missing")  # miss

        s = await c.stats()
        assert s["name"] == "my_cache"
        assert s["size"] == 1
        assert s["max_size"] == 10
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["evictions"] == 0
        assert s["expirations"] == 0
        assert "hit_rate" in s
        assert s["hit_rate"] == "50.00%"

    @pytest.mark.asyncio
    async def test_empty_cache_stats(self):
        c = ManagedCache(name="empty")
        s = await c.stats()
        assert s["size"] == 0
        assert s["hits"] == 0
        assert s["misses"] == 0
        assert s["hit_rate"] == "0.00%"

    @pytest.mark.asyncio
    async def test_hit_rate_after_evictions(self, monkeypatch, clock):
        """Evictions don't affect hit_rate (only hits/misses do)."""
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", max_size=2, default_ttl=100)
        await c.set("a", 1)
        await c.set("b", 2)

        await c.get("a")  # hit
        await c.set("c", 3)  # evicts "a" (not "b", since "a" was just accessed)
        # Actually wait: after get("a"), LRU order is b, a.
        # set("c") evicts "b" → "a" survives.
        await c.get("b")  # miss (b was evicted)

        # hits=1, misses=1 → hit_rate = 0.5
        s = await c.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["evictions"] == 1
        assert s["hit_rate"] == "50.00%"


# ── on_evict callback ───────────────────────────────────────────────────────


class TestOnEvictCallback:
    """Behaviour of the on_evict callback across all eviction paths."""

    @pytest.mark.asyncio
    async def test_on_evict_lru(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        evicted: list = []
        c = ManagedCache(
            name="test",
            max_size=1,
            default_ttl=100,
            on_evict=lambda k, v: evicted.append((k, v)),
        )
        await c.set("a", 1)
        await c.set("b", 2)
        assert evicted == [("a", 1)]

    @pytest.mark.asyncio
    async def test_on_evict_exception_does_not_crash_set(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        def bad_evict(k, v):
            raise RuntimeError("eviction failed")

        c = ManagedCache(
            name="test",
            max_size=1,
            default_ttl=100,
            on_evict=bad_evict,
        )
        await c.set("a", 1)
        # Must not raise
        await c.set("b", 2)
        assert await c.get("b") == 2

    @pytest.mark.asyncio
    async def test_on_evict_exception_does_not_crash_cleanup(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        called: list = []

        def bad_evict(k, v):
            called.append(k)
            raise RuntimeError("cleanup evict failed")

        c = ManagedCache(
            name="test",
            default_ttl=5.0,
            on_evict=bad_evict,
        )
        await c.set("a", 1)
        await c.set("b", 2)
        clock.advance(10.0)

        # Must not raise
        removed = await c.cleanup_expired()
        assert removed == 2
        assert set(called) == {"a", "b"}


# ── Edge Cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary conditions and special scenarios."""

    @pytest.mark.asyncio
    async def test_max_size_one_many_sets(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", max_size=1, default_ttl=100)
        for i in range(50):
            await c.set(f"k{i}", i)

        assert await c.size() == 1
        assert await c.get("k49") == 49

    @pytest.mark.asyncio
    async def test_different_key_types(self):
        c = ManagedCache(name="test")
        await c.set(123, "int_key")
        await c.set("str", "str_key")
        await c.set((1, 2), "tuple_key")

        assert await c.get(123) == "int_key"
        assert await c.get("str") == "str_key"
        assert await c.get((1, 2)) == "tuple_key"

    @pytest.mark.asyncio
    async def test_none_value_stored(self):
        """None is a valid cache value (ambiguous with miss via get())."""
        c = ManagedCache(name="test")
        await c.set("key", None)

        # get_metadata confirms key exists
        meta = await c.get_metadata("key")
        assert meta is not None

        # get() returns None — ambiguous with miss
        assert await c.get("key") is None

    @pytest.mark.asyncio
    async def test_repeated_invalidate_same_key(self):
        c = ManagedCache(name="test")
        await c.set("key", "value")
        assert await c.invalidate("key") is True
        assert await c.invalidate("key") is False  # already gone

    @pytest.mark.asyncio
    async def test_cleanup_preserves_non_expired(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=10.0)
        await c.set("a", 1)  # t=1000, expires 1010
        clock.advance(5.0)  # t=1005
        await c.set("b", 2)  # t=1005, expires 1015
        clock.advance(3.0)  # t=1008

        # Neither expired yet
        removed = await c.cleanup_expired()
        assert removed == 0
        assert await c.size() == 2

    @pytest.mark.asyncio
    async def test_stats_after_many_evictions(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", max_size=5, default_ttl=100)
        for i in range(10):
            await c.set(f"k{i}", i)

        s = await c.stats()
        assert s["size"] == 5
        assert s["evictions"] == 5  # 10 items, max_size=5

    @pytest.mark.asyncio
    async def test_set_with_explicit_ttl_override(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=100)
        await c.set("key", "value", ttl=3.0)

        clock.advance(2.0)
        assert await c.get("key") == "value"

        clock.advance(2.0)  # total 4 s > 3 s TTL
        assert await c.get("key") is None

    @pytest.mark.asyncio
    async def test_multiple_keys_same_expiry_batch_cleanup(self, monkeypatch, clock):
        monkeypatch.setattr(time, "monotonic", clock)

        c = ManagedCache(name="test", default_ttl=10.0)
        for i in range(5):
            await c.set(f"k{i}", i)

        clock.advance(15.0)
        removed = await c.cleanup_expired()
        assert removed == 5
        assert await c.size() == 0
        assert c.metrics.expirations == 5
