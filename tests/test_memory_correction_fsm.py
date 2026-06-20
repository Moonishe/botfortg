"""Tests for the FSM-based /memory --correct flow.

Covers the migration from the legacy ``_PENDING_CORRECTIONS`` dict + custom
``_PendingCorrectionFilter`` (FSM-lite) to native aiogram FSM via
``MemoryCorrectionStates.waiting_new_text``.

Verifies:
  * Writer site (``_cmd_memory_correct``) sets the FSM state + data.
  * Consumer handler (``handle_pending_correction``) reads state, processes
    the text, updates the DB, and clears the state.
  * ``cb_memreval`` cancel / reject / permanent branches clear the state
    after their action succeeds.
  * ``cb_memreval`` does NOT clear state for users not in
    ``MemoryCorrectionStates.waiting_new_text`` (no-op).
  * Lazy TTL via ``set_at_ts`` in state data — the consumer clears state
    when more than 300 seconds have passed.
"""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# ── Environment setup BEFORE importing src modules ──────────────────
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src.bot.handlers.memory_correction import (
    CORRECTION_TTL_SECONDS,
    handle_pending_correction,
    router,
)
from src.bot.handlers.memory_cmd import _cmd_memory_correct, cb_memreval
from src.bot.states import MemoryCorrectionStates
from src.db.models import Memory
from src.db.repo import get_or_create_user
from src.db.session import get_session, init_db

OWNER_TG_ID = 123456789


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
async def _fsm_db_setup():
    """Recreate tables before each FSM test; other tests may dispose the global engine."""
    from src.db.session import engine, Base
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
    await init_db()
    yield


BOT_ID = 0  # sentinel — MemoryStorage doesn't check it
EXPECTED_STATE = MemoryCorrectionStates.waiting_new_text.state


# ── Helpers ─────────────────────────────────────────────────────────


def _make_message(user_id: int = OWNER_TG_ID, text: str = "новый текст") -> MagicMock:
    """Mock aiogram Message for the consumer handler."""
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _make_callback(
    user_id: int = OWNER_TG_ID, data: str = "memreval:cancel"
) -> MagicMock:
    """Mock aiogram CallbackQuery for cb_memreval."""
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _make_user_fsm(user_id: int = OWNER_TG_ID) -> tuple[MemoryStorage, FSMContext]:
    """Build a (storage, FSMContext) pair for the given user."""
    storage = MemoryStorage()
    key = StorageKey(bot_id=BOT_ID, chat_id=user_id, user_id=user_id)
    ctx = FSMContext(storage=storage, key=key)
    return storage, ctx


# ── Fixtures ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def owner_with_fact():
    """Create an owner user + a Memory row owned by them; return (user, memory).

    Ensures DB tables exist on the current event loop (workaround for
    session-scoped _db_init using asyncio.run() which may lose tables
    after many tests on different event loops).
    """
    from src.db.session import engine, Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        fact = Memory(
            user_id=owner.id,
            fact="оригинальный факт для исправления",
            memory_type="contact_fact",
            sentiment="neutral",
            is_active=True,
        )
        session.add(fact)
        await session.commit()
        await session.refresh(fact)
        return owner.id, fact.id, fact.fact


# ═══════════════════════════════════════════════════════════════════
#  Tests: writer sets FSM state + data
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("_db_init")
class TestWriterSetsState:
    """_cmd_memory_correct must set the FSM state and stash data."""

    @pytest.mark.asyncio
    async def test_sets_state_and_stores_data(self, owner_with_fact):
        owner_id, fact_id, _ = owner_with_fact
        storage, state = _make_user_fsm()

        msg = _make_message()
        await _cmd_memory_correct(msg, f"--correct {fact_id}", state)

        # State should now be set
        current = await state.get_state()
        assert current == EXPECTED_STATE, f"Expected {EXPECTED_STATE}, got {current}"

        # Data should contain memory_id, original_fact, set_at_ts
        data = await state.get_data()
        assert data.get("memory_id") == fact_id
        assert data.get("original_fact") == "оригинальный факт для исправления"
        assert "set_at_ts" in data
        # set_at_ts is a recent monotonic timestamp (within last 5 seconds)
        assert 0 <= time.monotonic() - data["set_at_ts"] < 5

    @pytest.mark.asyncio
    async def test_does_not_set_state_for_missing_id(self, owner_with_fact):
        storage, state = _make_user_fsm()
        msg = _make_message()

        await _cmd_memory_correct(msg, "--correct 99999", state)

        current = await state.get_state()
        assert current is None, "State must NOT be set for non-existent memory id"


# ═══════════════════════════════════════════════════════════════════
#  Tests: consumer processes text and clears state
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("_db_init")
class TestConsumerFlow:
    """handle_pending_correction must read state, update DB, clear state."""

    @pytest.mark.asyncio
    async def test_happy_path_updates_db_and_clears_state(self, owner_with_fact):
        owner_id, fact_id, original = owner_with_fact
        storage, state = _make_user_fsm()

        # Writer sets the state
        msg = _make_message()
        await _cmd_memory_correct(msg, f"--correct {fact_id}", state)
        assert await state.get_state() == EXPECTED_STATE

        # Consumer handles the new text
        new_msg = _make_message(text="исправленный текст")
        await handle_pending_correction(new_msg, state)

        # State must be cleared
        current = await state.get_state()
        assert current is None, f"State should be cleared, got {current}"

        # DB must be updated
        async with get_session() as session:
            updated = await session.get(Memory, fact_id)
            assert updated is not None
            assert updated.fact == "исправленный текст"
            assert updated.fact != original  # changed

    @pytest.mark.asyncio
    async def test_short_text_does_not_clear_state(self, owner_with_fact):
        """Text shorter than 3 chars keeps state — user gets a retry prompt."""
        owner_id, fact_id, _ = owner_with_fact
        storage, state = _make_user_fsm()

        msg = _make_message()
        await _cmd_memory_correct(msg, f"--correct {fact_id}", state)
        assert await state.get_state() == EXPECTED_STATE

        new_msg = _make_message(text="ab")  # < 3 chars
        await handle_pending_correction(new_msg, state)

        # State should still be set (user retries)
        current = await state.get_state()
        assert current == EXPECTED_STATE

        # DB must NOT be updated
        async with get_session() as session:
            updated = await session.get(Memory, fact_id)
            assert updated.fact == "оригинальный факт для исправления"

    @pytest.mark.asyncio
    async def test_long_text_does_not_clear_state(self, owner_with_fact):
        """Text longer than 500 chars keeps state — user gets a retry prompt."""
        owner_id, fact_id, _ = owner_with_fact
        storage, state = _make_user_fsm()

        msg = _make_message()
        await _cmd_memory_correct(msg, f"--correct {fact_id}", state)
        assert await state.get_state() == EXPECTED_STATE

        long_text = "x" * 501
        new_msg = _make_message(text=long_text)
        await handle_pending_correction(new_msg, state)

        current = await state.get_state()
        assert current == EXPECTED_STATE, "Long text should keep state for retry"


# ═══════════════════════════════════════════════════════════════════
#  Tests: lazy TTL — set_at_ts older than 300s expires state
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("_db_init")
class TestLazyTTL:
    """If set_at_ts is older than CORRECTION_TTL_SECONDS, state is cleared."""

    @pytest.mark.asyncio
    async def test_expired_state_is_cleared(self, owner_with_fact):
        owner_id, fact_id, _ = owner_with_fact
        storage, state = _make_user_fsm()

        msg = _make_message()
        await _cmd_memory_correct(msg, f"--correct {fact_id}", state)
        assert await state.get_state() == EXPECTED_STATE

        # Simulate 301 seconds passing by rewriting set_at_ts
        await state.update_data(
            set_at_ts=time.monotonic() - (CORRECTION_TTL_SECONDS + 1)
        )

        new_msg = _make_message(text="опоздавший текст")
        await handle_pending_correction(new_msg, state)

        current = await state.get_state()
        assert current is None, "Expired state should be cleared by TTL check"

        # DB must NOT be updated
        async with get_session() as session:
            updated = await session.get(Memory, fact_id)
            assert updated.fact == "оригинальный факт для исправления"

    @pytest.mark.asyncio
    async def test_fresh_state_is_processed(self, owner_with_fact):
        owner_id, fact_id, _ = owner_with_fact
        storage, state = _make_user_fsm()

        msg = _make_message()
        await _cmd_memory_correct(msg, f"--correct {fact_id}", state)

        # set_at_ts is fresh (set by writer seconds ago)
        new_msg = _make_message(text="свежий текст")
        await handle_pending_correction(new_msg, state)

        # State should be cleared
        assert await state.get_state() is None

        # DB should be updated
        async with get_session() as session:
            updated = await session.get(Memory, fact_id)
            assert updated.fact == "свежий текст"


# ═══════════════════════════════════════════════════════════════════
#  Tests: cb_memreval cancel / reject / permanent clear state
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("_db_init")
class TestCbMemrevalClearsState:
    """cb_memreval must clear MemoryCorrectionStates.waiting_new_text state."""

    @pytest.mark.asyncio
    async def test_cancel_clears_state(self, owner_with_fact):
        owner_id, fact_id, _ = owner_with_fact
        storage, state = _make_user_fsm()
        msg = _make_message()
        await _cmd_memory_correct(msg, f"--correct {fact_id}", state)
        assert await state.get_state() == EXPECTED_STATE

        cb = _make_callback(data="memreval:cancel")
        await cb_memreval(cb, state)

        assert await state.get_state() is None, "Cancel must clear state"

    @pytest.mark.asyncio
    async def test_reject_clears_state_and_deactivates_fact(self, owner_with_fact):
        owner_id, fact_id, _ = owner_with_fact
        storage, state = _make_user_fsm()
        msg = _make_message()
        await _cmd_memory_correct(msg, f"--correct {fact_id}", state)
        assert await state.get_state() == EXPECTED_STATE

        cb = _make_callback(data=f"memreval:reject:{fact_id}")
        await cb_memreval(cb, state)

        assert await state.get_state() is None, "Reject must clear state"

        async with get_session() as session:
            updated = await session.get(Memory, fact_id)
            assert updated.is_active is False, "Reject must deactivate the fact"

    @pytest.mark.asyncio
    async def test_permanent_clears_state_and_pins_fact(self, owner_with_fact):
        owner_id, fact_id, _ = owner_with_fact
        storage, state = _make_user_fsm()
        msg = _make_message()
        await _cmd_memory_correct(msg, f"--correct {fact_id}", state)
        assert await state.get_state() == EXPECTED_STATE

        cb = _make_callback(data=f"memreval:permanent:{fact_id}")
        await cb_memreval(cb, state)

        assert await state.get_state() is None, "Permanent must clear state"

        async with get_session() as session:
            updated = await session.get(Memory, fact_id)
            assert updated.pinned is True, "Permanent must pin the fact"

    @pytest.mark.asyncio
    async def test_cancel_for_user_not_in_state_is_noop(self):
        """User with no FSM state — cb_memreval:cancel must not raise or clear."""
        storage, state = _make_user_fsm(user_id=99999)
        # No writer call — state is None

        cb = _make_callback(user_id=99999, data="memreval:cancel")
        await cb_memreval(cb, state)

        # State stays None (no-op)
        assert await state.get_state() is None
        # The cancel branch is hit and edit_text is called
        cb.message.edit_text.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
#  Tests: no global _PENDING_CORRECTIONS dict or _PendingCorrectionFilter
# ═══════════════════════════════════════════════════════════════════


class TestLegacyRemoved:
    """Legacy FSM-lite artifacts must be gone from memory_cmd module."""

    def test_no_pending_corrections_dict(self):
        from src.bot import handlers

        # Use module-level access via importlib
        import importlib

        memory_cmd = importlib.import_module("src.bot.handlers.memory_cmd")
        assert not hasattr(memory_cmd, "_PENDING_CORRECTIONS"), (
            "_PENDING_CORRECTIONS dict must be removed"
        )
        assert not hasattr(memory_cmd, "_PendingCorrectionFilter"), (
            "_PendingCorrectionFilter class must be removed"
        )
        assert not hasattr(memory_cmd, "cmd_cancel_pending"), (
            "cmd_cancel_pending must be removed (global /cancel handles this)"
        )
        assert not hasattr(memory_cmd, "handle_pending_correction"), (
            "handle_pending_correction must be moved to memory_correction module"
        )
        assert not hasattr(memory_cmd, "BaseFilter"), (
            "BaseFilter import must be removed (no longer used)"
        )

    def test_state_module_has_correction_states(self):
        from src.bot.states import MemoryCorrectionStates

        assert hasattr(MemoryCorrectionStates, "waiting_new_text")
        assert MemoryCorrectionStates.waiting_new_text.state == EXPECTED_STATE

    def test_correction_router_has_owner_filter(self):
        from src.bot.handlers.memory_correction import router

        # Router should have a message filter chain that includes OwnerOnly
        assert router.name == "memory_correction"
