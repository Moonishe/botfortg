"""Tests for CRIU-style snapshot engine (Phase 2.2).

Covers:
  - capture() returns expected keys
  - save_to_disk() creates file
  - restore() loads from file
  - restore() returns False when no file or too old
  - conversation context capture/restore round-trip
  - circuit breaker cooldown expiry on restore
"""

from __future__ import annotations

import json
import os
import time

import pytest

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef")

from src.core.state.snapshot_engine import SnapshotEngine, snapshot_engine


@pytest.fixture
def fresh_engine(tmp_path):
    """Engine with isolated snapshot path."""
    test_path = str(tmp_path / "snapshot.json")
    # Patch via sys.modules to avoid shadowing from __init__.py's snapshot_engine instance
    import sys

    snap_mod = sys.modules["src.core.state.snapshot_engine"]
    old_path = snap_mod._SNAPSHOT_PATH
    snap_mod._SNAPSHOT_PATH = test_path
    try:
        yield SnapshotEngine(), test_path
    finally:
        snap_mod._SNAPSHOT_PATH = old_path


@pytest.mark.asyncio
async def test_capture_returns_expected_keys():
    """capture() returns a dict with expected top-level keys."""
    engine = SnapshotEngine()
    snap = await engine.capture()
    assert isinstance(snap, dict)
    assert "timestamp" in snap
    assert "version" in snap
    assert snap["version"] == 1
    assert "conversation_context" in snap
    assert "active_sessions" in snap
    assert "pending_questions" in snap
    assert "circuit_breakers" in snap


@pytest.mark.asyncio
async def test_save_to_disk_creates_file(fresh_engine):
    """save_to_disk() creates a JSON file."""
    engine, path = fresh_engine
    await engine.save_to_disk()
    assert os.path.exists(path)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert "timestamp" in data
    assert data["version"] == 1


@pytest.mark.asyncio
async def test_restore_loads_from_file(fresh_engine):
    """restore() loads a saved snapshot."""
    engine, path = fresh_engine
    snap = await engine.capture()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, default=str)

    result = await engine.restore()
    assert result is True


@pytest.mark.asyncio
async def test_restore_false_when_no_file(fresh_engine):
    """restore() returns False when no snapshot file exists."""
    engine, path = fresh_engine
    # Ensure file doesn't exist
    if os.path.exists(path):
        os.remove(path)
    result = await engine.restore()
    assert result is False


@pytest.mark.asyncio
async def test_restore_false_when_too_old(fresh_engine, monkeypatch):
    """restore() returns False when snapshot is older than max age."""
    engine, path = fresh_engine
    # Create snapshot with old timestamp
    old_snap = {
        "timestamp": "2020-01-01T00:00:00+00:00",
        "version": 1,
        "conversation_context": {},
        "active_sessions": {},
        "pending_questions": {},
        "circuit_breakers": {},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(old_snap, f)

    result = await engine.restore()
    assert result is False


@pytest.mark.asyncio
async def test_conversation_context_round_trip():
    """Capture and restore conversation_context round-trip."""
    from src.core.memory.conversation_context import (
        _STORE,
        _ctx_lock,
        _Ctx,
        add_turn,
        set_last_peer,
        set_last_purpose,
    )

    # Clean up before test
    async with _ctx_lock:
        _STORE.clear()

    # Populate context
    telegram_id = 99999
    await add_turn(telegram_id, "Hello", "Hi there!")
    await add_turn(telegram_id, "What's up?", "Not much")
    await set_last_peer(telegram_id, 12345, "Alice")
    await set_last_purpose(telegram_id, "Testing")

    # Capture
    engine = SnapshotEngine()
    snap = await engine.capture()
    ctx_data = snap["conversation_context"].get(str(telegram_id))
    assert ctx_data is not None
    assert len(ctx_data["turns"]) == 2
    assert ctx_data["last_peer_id"] == 12345
    assert ctx_data["last_peer_name"] == "Alice"
    assert ctx_data["last_purpose"] == "Testing"

    # Clear and restore
    async with _ctx_lock:
        _STORE.clear()
    assert len(_STORE) == 0

    await engine._restore_conversation_context(snap["conversation_context"])
    assert telegram_id in _STORE
    restored = _STORE[telegram_id]
    assert len(restored.turns) == 2
    assert restored.last_peer_id == 12345
    assert restored.last_peer_name == "Alice"
    assert restored.last_purpose == "Testing"


@pytest.mark.asyncio
async def test_circuit_breaker_cooldown_expiry():
    """Circuit breaker OPEN state transitions to HALF_OPEN on restore if cooldown expired."""
    from src.core.actions.tool_middleware import ToolCircuitBreaker, CBState

    # Clean up before test
    async with ToolCircuitBreaker._lock:
        ToolCircuitBreaker._states.clear()

    engine = SnapshotEngine()

    # Simulate an old OPEN state (opened 200s ago, cooldown is 120s)
    now = time.monotonic()
    old_opened_at = now - 200.0
    data = {
        "test_tool": {
            "failures": 5,
            "state": "OPEN",
            "opened_at": old_opened_at,
        }
    }

    await engine._restore_circuit_breakers(data)

    async with ToolCircuitBreaker._lock:
        state = ToolCircuitBreaker._states.get("test_tool")
    assert state is not None
    # Should be HALF_OPEN because cooldown expired
    assert state.state == "HALF_OPEN"
    assert state.failures == 5


@pytest.mark.asyncio
async def test_circuit_breaker_still_open():
    """Circuit breaker remains OPEN if cooldown hasn't expired yet."""
    from src.core.actions.tool_middleware import ToolCircuitBreaker, CBState

    # Clean up before test
    async with ToolCircuitBreaker._lock:
        ToolCircuitBreaker._states.clear()

    engine = SnapshotEngine()

    # Simulate a recent OPEN state (opened 10s ago)
    now = time.monotonic()
    recent_opened_at = now - 10.0
    data = {
        "test_tool": {
            "failures": 5,
            "state": "OPEN",
            "opened_at": recent_opened_at,
        }
    }

    await engine._restore_circuit_breakers(data)

    async with ToolCircuitBreaker._lock:
        state = ToolCircuitBreaker._states.get("test_tool")
    assert state is not None
    # Should still be OPEN because cooldown hasn't expired
    assert state.state == "OPEN"


@pytest.mark.asyncio
async def test_restore_empty_context_graceful():
    """Restore handles empty context data gracefully."""
    engine = SnapshotEngine()
    # Should not raise
    await engine._restore_conversation_context({})
    await engine._restore_active_sessions({})
    await engine._restore_pending_questions({})
    await engine._restore_circuit_breakers({})


@pytest.mark.asyncio
async def test_save_and_restore_integration(fresh_engine):
    """Integration: save → clear → restore cycle."""
    from src.core.memory.conversation_context import (
        _STORE,
        _ctx_lock,
        add_turn,
        set_last_peer,
    )

    engine, path = fresh_engine

    # Clean and populate
    async with _ctx_lock:
        _STORE.clear()

    telegram_id = 88888
    await add_turn(telegram_id, "Integration test", "Works")

    # Save
    await engine.save_to_disk()
    assert os.path.exists(path)

    # Clear
    async with _ctx_lock:
        _STORE.clear()
    assert len(_STORE) == 0

    # Restore
    restored = await engine.restore()
    assert restored is True
    assert telegram_id in _STORE
    assert len(_STORE[telegram_id].turns) == 1
