"""Test DEK rotation pool LRU eviction."""

from src.core.crypto.key_rotation import KeyRotationManager


class TestKeyRotationPool:
    """Verify DEK cache does not grow unbounded."""

    def test_dek_cache_bounded_after_rotations(self):
        """After 15 rotations, len(manager._deks) <= _MAX_CACHED_DEKS."""
        from cryptography.fernet import Fernet

        kek = Fernet.generate_key()
        mgr = KeyRotationManager(kek)
        max_deks = mgr._MAX_CACHED_DEKS

        # Seed an initial DEK so _rotate_unlocked has an active key.
        mgr._deks[0] = Fernet.generate_key()
        mgr._active_key_id = 0

        # Simulate 15 rotations (needs _lock acquired for _rotate_unlocked)
        import asyncio

        async def _rotate_15():
            async with mgr._lock:
                for _ in range(15):
                    await mgr._rotate_unlocked()

        asyncio.run(_rotate_15())

        assert len(mgr._deks) <= max_deks, (
            f"Expected <= {max_deks} cached DEKs, got {len(mgr._deks)}"
        )
        # Active key should still be cached
        assert mgr._active_key_id in mgr._deks
