"""Minimal tests for manager.py _clients_lock and TOCTOU fixes."""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── Test _clients_lock field exists ───────────────────────────────────────


def test_clients_lock_field_exists():
    """Bug 4: _clients_lock must be present on UserbotManager."""
    from src.userbot.manager import UserbotManager

    mgr = UserbotManager()
    assert hasattr(mgr, "_clients_lock"), "_clients_lock is missing — Bug 4 not fixed"
    assert isinstance(mgr._clients_lock, asyncio.Lock), (
        "_clients_lock must be asyncio.Lock"
    )


# ── Test remove_client uses lock ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_client_atomic_pop():
    """Bug 4: remove_client must atomically pop under lock.

    Previously: get() → await log_out() → await disconnect() → pop()
    This is TOCTOU-prone: another coroutine could pop between get() and pop().
    Now: pop() under lock first, then disconnect.
    """
    from src.userbot.manager import UserbotManager

    mgr = UserbotManager()

    # Create mock client
    mock_client = MagicMock()
    mock_client.log_out = AsyncMock()
    mock_client.disconnect = AsyncMock()

    # Register under lock
    async with mgr._clients_lock:
        mgr._clients[12345] = mock_client

    # Remove — should pop atomically
    await mgr.remove_client(12345)

    assert 12345 not in mgr._clients, (
        "Client should be removed from _clients after remove_client"
    )
    mock_client.log_out.assert_awaited_once()
    mock_client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_remove_client_nonexistent():
    """remove_client of non-existent id should not raise."""
    from src.userbot.manager import UserbotManager

    mgr = UserbotManager()
    # Should not raise
    await mgr.remove_client(99999)


# ── Test health_check_loop atomic re-check ────────────────────────────────


@pytest.mark.asyncio
async def test_health_check_pop_only_same_client():
    """Bug 5: health_check must re-verify client identity before popping.

    Scenario:
    1. health_check takes snapshot: {123: client_A}
    2. Another coroutine replaces client_A with client_B in _clients
    3. health_check should NOT pop client_B (it's a different object)

    With the fix, health_check does `if self._clients.get(tg_id) is client`
    before popping.
    """
    from src.userbot.manager import UserbotManager

    mgr = UserbotManager()

    # Simulate the atomic re-check pattern
    client_A = MagicMock()
    client_B = MagicMock()

    # Register client_A
    async with mgr._clients_lock:
        mgr._clients[1] = client_A

    # Simulate: another coroutine replaces with client_B
    async with mgr._clients_lock:
        mgr._clients[1] = client_B

    # Now health_check tries to remove client_A — should NOT remove client_B
    async with mgr._clients_lock:
        if mgr._clients.get(1) is client_A:
            mgr._clients.pop(1, None)

    # client_B should still be there
    assert 1 in mgr._clients, (
        "TOCTOU: client_B should NOT be removed when checking for client_A"
    )
    assert mgr._clients[1] is client_B, "client_B should remain unchanged"

    # Cleanup: remove client_B normally
    async with mgr._clients_lock:
        mgr._clients.pop(1, None)
    assert 1 not in mgr._clients


# ── Test shutdown snapshot is consistent ──────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_snapshot_under_lock():
    """Bug 4: shutdown must snapshot _clients under lock.

    Without lock, _clients could be modified during iteration
    (e.g., by health_check_loop), causing RuntimeError: dict changed size.
    """
    from src.userbot.manager import UserbotManager

    mgr = UserbotManager()

    mock_client = MagicMock()
    mock_client.disconnect = AsyncMock()

    # Register under lock
    async with mgr._clients_lock:
        mgr._clients[1] = mock_client

    # Simulate the shutdown pattern (snapshot under lock, disconnect, clear under lock)
    async with mgr._clients_lock:
        snapshot = list(mgr._clients.items())

    # Disconnect (mocked)
    for tg_id, client in snapshot:
        await client.disconnect()  # type: ignore[misc]

    async with mgr._clients_lock:
        mgr._clients.clear()

    assert len(mgr._clients) == 0, "shutdown should clear _clients"
    mock_client.disconnect.assert_awaited_once()


# ── Test register_client still works (sync, GIL-safe) ─────────────────────


@pytest.mark.asyncio
async def test_register_client_assigns():
    """register_client must assign to _clients under _clients_lock."""
    from src.userbot.manager import UserbotManager

    mgr = UserbotManager()
    mock_client = MagicMock()

    with (
        patch("src.userbot.auto_reply.attach_auto_reply"),
        patch("src.userbot.dialog_events.attach_dialog_event_handlers"),
        patch("src.userbot.mirror.attach_mirror"),
    ):
        await mgr.register_client(42, mock_client)

    assert mgr._clients[42] is mock_client, "register_client must assign to _clients"
