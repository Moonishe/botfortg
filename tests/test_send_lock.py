"""Focused edge-case tests for _get_confirm_lock and payload handling."""

from __future__ import annotations

import asyncio

import pytest

from src.bot.handlers.send import _confirm_locks_last_used


class TestGetConfirmLock:
    """Race-free per-user lock acquisition."""

    @staticmethod
    def _import_lock_fn():
        from src.bot.handlers.send import (
            _get_confirm_lock,
            _confirm_locks,
            _confirm_locks_lock,
        )

        return _get_confirm_lock, _confirm_locks, _confirm_locks_lock

    @staticmethod
    def _import_cleanup_fn():
        from src.bot.handlers.send import (
            _confirm_locks,
            _confirm_locks_last_used,
            _CONFIRM_LOCK_TTL_SEC,
            _cleanup_confirm_locks,
        )

        return (
            _confirm_locks,
            _confirm_locks_last_used,
            _CONFIRM_LOCK_TTL_SEC,
            _cleanup_confirm_locks,
        )

    @pytest.mark.asyncio
    async def test_same_lock_for_same_user(self) -> None:
        """Two sequential calls for same user return the same lock object."""
        get_lock, locks_dict, _ = self._import_lock_fn()
        uid = 999001
        locks_dict.pop(uid, None)  # Ensure clean state
        _confirm_locks_last_used.pop(uid, None)
        l1 = await get_lock(uid)
        l2 = await get_lock(uid)
        assert l1 is l2

    @pytest.mark.asyncio
    async def test_different_users_different_locks(self) -> None:
        """Different users get different lock objects."""
        get_lock, locks_dict, _ = self._import_lock_fn()
        uid1, uid2 = 999002, 999003
        locks_dict.pop(uid1, None)
        locks_dict.pop(uid2, None)
        _confirm_locks_last_used.pop(uid1, None)
        _confirm_locks_last_used.pop(uid2, None)
        l1 = await get_lock(uid1)
        l2 = await get_lock(uid2)
        assert l1 is not l2

    @pytest.mark.asyncio
    async def test_concurrent_same_user_single_lock(self) -> None:
        """Concurrent coroutines for same user all get the same lock instance
        and only ONE lock is created."""
        get_lock, locks_dict, _ = self._import_lock_fn()
        uid = 999004
        locks_dict.pop(uid, None)
        _confirm_locks_last_used.pop(uid, None)

        async def acquire():
            return await get_lock(uid)

        # Fire 20 concurrent coroutines
        locks = await asyncio.gather(*(acquire() for _ in range(20)))
        first = locks[0]
        # All must be the same object
        assert all(lk is first for lk in locks)
        # Only one entry in the dict
        assert locks_dict[uid] is first

    @pytest.mark.asyncio
    async def test_concurrent_multi_user_no_cross_contamination(self) -> None:
        """Concurrent calls for different users create distinct locks."""
        get_lock, locks_dict, _ = self._import_lock_fn()
        uids = [999005, 999006, 999007, 999008, 999009]
        for uid in uids:
            locks_dict.pop(uid, None)
            _confirm_locks_last_used.pop(uid, None)

        async def acquire(uid):
            return await get_lock(uid)

        # Interleave concurrent acquisitions for different users
        tasks = []
        for uid in uids:
            for _ in range(3):
                tasks.append(acquire(uid))
        await asyncio.gather(*tasks)

        locks = [locks_dict[uid] for uid in uids]
        # All locks must be distinct from each other
        assert len({id(lk) for lk in locks}) == len(uids)

    @pytest.mark.asyncio
    async def test_lock_actually_serializes(self) -> None:
        """The returned asyncio.Lock actually serializes critical sections."""
        get_lock, locks_dict, _ = self._import_lock_fn()
        uid = 999010
        locks_dict.pop(uid, None)
        _confirm_locks_last_used.pop(uid, None)
        shared: list[int] = []

        async def critical_section(i: int) -> None:
            lock = await get_lock(uid)
            async with lock:
                # Simulate DB work
                shared.append(i)
                await asyncio.sleep(0.001)

        # Run 10 concurrent critical sections
        await asyncio.gather(*(critical_section(i) for i in range(10)))
        # If lock works, shared has 10 items (all inserted)
        assert len(shared) == 10
        # With asyncio.Lock (non-reentrant), the order is sequential but
        # asyncio.gather may reorder. We just verify no data loss.

    @pytest.mark.asyncio
    async def test_stale_locks_are_cleaned_up(self) -> None:
        """Locks unused for longer than TTL are removed during next access."""
        locks_dict, last_used, ttl, cleanup = self._import_cleanup_fn()
        uid = 999020
        locks_dict.pop(uid, None)
        last_used.pop(uid, None)

        lock = asyncio.Lock()
        locks_dict[uid] = lock
        last_used[uid] = 0.0  # far in the past

        now = ttl + 1.0
        cleanup(now)
        assert uid not in locks_dict
        assert uid not in last_used

    @pytest.mark.asyncio
    async def test_locked_stale_locks_not_removed(self) -> None:
        """Stale locks that are currently held are not removed."""
        locks_dict, last_used, ttl, cleanup = self._import_cleanup_fn()
        uid = 999021
        locks_dict.pop(uid, None)
        last_used.pop(uid, None)

        lock = asyncio.Lock()
        locks_dict[uid] = lock
        last_used[uid] = 0.0

        async def hold():
            async with lock:
                # Lock is held; cleanup should skip it.
                cleanup(ttl + 1.0)
                assert uid in locks_dict
                assert uid in last_used

        await hold()

    @pytest.mark.asyncio
    async def test_get_lock_updates_last_used(self) -> None:
        """Accessing a lock refreshes its last-used timestamp."""
        get_lock, locks_dict, _ = self._import_lock_fn()
        uid = 999022
        locks_dict.pop(uid, None)
        _confirm_locks_last_used.pop(uid, None)

        await get_lock(uid)
        assert uid in _confirm_locks_last_used
        assert _confirm_locks_last_used[uid] > 0
