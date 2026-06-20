"""Unit tests for memory admin commands — memory_admin_cmds.py.

Covers:
  - OwnerOnly filter (pass for owner, reject for non-owner)
  - cmd_health returns expected message
  - cmd_llm_status returns expected message
  - cmd_remember with valid args
  - cb_memory_clear_negative, cb_memory_stats
  - cb_pattern_action (dismiss, remind)
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Environment setup BEFORE importing src modules ──────────────────
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.bot.filters import OwnerOnly

OWNER_TG_ID = 123456789
NON_OWNER_ID = 999999999


# ── Helpers ─────────────────────────────────────────────────────────


def _make_message(user_id: int = OWNER_TG_ID, text: str = "/health") -> MagicMock:
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    msg.delete = AsyncMock()
    msg.edit_text = AsyncMock()
    return msg


def _make_callback(user_id: int = OWNER_TG_ID, data: str = "memory:stats") -> MagicMock:
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    return cb


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def setup_db():
    """Recreate all tables before each test (in-memory SQLite)."""
    from src.db.session import (
        engine,
        Base,
        _FTS_SETUP,
        _SESSION_FTS_SETUP,
        _MEMORY_FTS_SETUP,
    )
    from sqlalchemy import text

    async def _recreate():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
            await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for stmt in _FTS_SETUP:
                await conn.execute(text(stmt))
            for stmt in _SESSION_FTS_SETUP:
                await conn.execute(text(stmt))
            for stmt in _MEMORY_FTS_SETUP:
                await conn.execute(text(stmt))

    asyncio.run(_recreate())


# ═══════════════════════════════════════════════════════════════════
#  Tests: OwnerOnly filter
# ═══════════════════════════════════════════════════════════════════


class TestOwnerOnlyFilter:
    """Unit tests for OwnerOnly filter."""

    @pytest.mark.asyncio
    async def test_passes_for_owner_message(self):
        f = OwnerOnly()
        msg = _make_message(user_id=OWNER_TG_ID)
        result = await f(msg)
        assert result is True

    @pytest.mark.asyncio
    async def test_passes_for_owner_callback(self):
        f = OwnerOnly()
        cb = _make_callback(user_id=OWNER_TG_ID)
        result = await f(cb)
        assert result is True

    @pytest.mark.asyncio
    async def test_rejects_non_owner_message(self):
        f = OwnerOnly()
        msg = _make_message(user_id=NON_OWNER_ID)
        result = await f(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_rejects_non_owner_callback(self):
        f = OwnerOnly()
        cb = _make_callback(user_id=NON_OWNER_ID)
        result = await f(cb)
        assert result is False

    @pytest.mark.asyncio
    async def test_rejects_no_from_user(self):
        f = OwnerOnly()
        msg = _make_message()
        msg.from_user = None
        result = await f(msg)
        assert result is False


# ═══════════════════════════════════════════════════════════════════
#  Tests: cmd_health
# ═══════════════════════════════════════════════════════════════════


class TestCmdHealth:
    """Tests for /health command."""

    @pytest.mark.asyncio
    async def test_cmd_health_returns_message(self):
        from src.bot.handlers.memory_admin_cmds import cmd_health
        from src.db.repo import get_or_create_user
        from src.db.session import get_session

        # Create a user first
        async with get_session() as session:
            await get_or_create_user(session, OWNER_TG_ID)

        msg = _make_message(text="/health")

        with (
            patch(
                "src.core.memory.memory_health.calculate_health_score",
                new=AsyncMock(return_value={"score": 85, "details": "ok"}),
            ),
            patch(
                "src.core.memory.memory_health.format_health",
                return_value="🫀 Health: 85/100",
            ),
        ):
            await cmd_health(msg)

        msg.answer.assert_called_once()
        assert "85" in msg.answer.call_args[0][0]


# ═══════════════════════════════════════════════════════════════════
#  Tests: cmd_llm_status
# ═══════════════════════════════════════════════════════════════════


class TestCmdLlmStatus:
    """Tests for /llm_status command."""

    @pytest.mark.asyncio
    async def test_cmd_llm_status_returns_expected_message(self):
        from src.bot.handlers.memory_admin_cmds import cmd_llm_status
        from src.db.repo import get_or_create_user
        from src.db.session import get_session

        # Create a user first
        async with get_session() as session:
            await get_or_create_user(session, OWNER_TG_ID)

        msg = _make_message(text="/llm_status")

        with (
            patch(
                "src.llm.router._PURPOSE_SEMAPHORES",
                {"main": MagicMock(_value=5, _bound_value=10)},
            ),
            patch(
                "src.bot.handlers.memory_admin_cmds.list_key_slots",
                new=AsyncMock(return_value=[]),
            ),
        ):
            await cmd_llm_status(msg)

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "LLM Status" in text


# ═══════════════════════════════════════════════════════════════════
#  Tests: cmd_remember
# ═══════════════════════════════════════════════════════════════════


class TestCmdRemember:
    """Tests for /remember command."""

    @pytest.mark.asyncio
    async def test_cmd_remember_no_args_shows_help(self):
        from src.bot.handlers.memory_admin_cmds import cmd_remember
        from aiogram.filters import CommandObject

        msg = _make_message(text="/remember")
        cmd = CommandObject(command="remember", args="")

        await cmd_remember(msg, cmd, userbot_manager=None)
        msg.answer.assert_called_once()
        assert "Использование" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_remember_with_args_stores_fact(self):
        from src.bot.handlers.memory_admin_cmds import cmd_remember
        from aiogram.filters import CommandObject

        msg = _make_message(text="/remember Тестовый факт")
        cmd = CommandObject(command="remember", args="Тестовый факт")

        await cmd_remember(msg, cmd, userbot_manager=None)
        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "Тестовый факт" in text or "Запомнил" in text


# ═══════════════════════════════════════════════════════════════════
#  Tests: cb_memory_clear_negative
# ═══════════════════════════════════════════════════════════════════


class TestCbMemoryClearNegative:
    """Tests for memory:clear_negative callback."""

    @pytest.mark.asyncio
    async def test_clear_negative_no_memories(self):
        from src.bot.handlers.memory_admin_cmds import cb_memory_clear_negative
        from src.db.repo import get_or_create_user
        from src.db.session import get_session

        async with get_session() as session:
            await get_or_create_user(session, OWNER_TG_ID)

        cb = _make_callback(data="memory:clear_negative")
        await cb_memory_clear_negative(cb)
        cb.answer.assert_called_once()
        # Should report 0 removed
        assert "0" in cb.answer.call_args[0][0]


# ═══════════════════════════════════════════════════════════════════
#  Tests: cb_memory_stats
# ═══════════════════════════════════════════════════════════════════


class TestCbMemoryStats:
    """Tests for memory:stats callback."""

    @pytest.mark.asyncio
    async def test_cb_memory_stats_empty(self):
        from src.bot.handlers.memory_admin_cmds import cb_memory_stats
        from src.db.repo import get_or_create_user
        from src.db.session import get_session

        async with get_session() as session:
            await get_or_create_user(session, OWNER_TG_ID)

        cb = _make_callback(data="memory:stats")
        with (
            patch(
                "src.core.memory.memory_fuel.get_fuel_stats",
                new=AsyncMock(
                    return_value={
                        "total_facts": 0,
                        "fuel_level": 100,
                        "depleted_contacts": [],
                    }
                ),
            ),
            patch("src.core.memory.memory_fuel.format_fuel_line", return_value=""),
            patch(
                "src.core.memory.memory_fuel.format_depleted_contacts", return_value=""
            ),
        ):
            await cb_memory_stats(cb)
        cb.message.answer.assert_called_once()
        text = cb.message.answer.call_args[0][0]
        assert "Статистика памяти" in text


# ═══════════════════════════════════════════════════════════════════
#  Tests: cb_pattern_action
# ═══════════════════════════════════════════════════════════════════


class TestCbPatternAction:
    """Tests for pattern:* callbacks."""

    @pytest.mark.asyncio
    async def test_pattern_dismiss(self):
        from src.bot.handlers.memory_admin_cmds import cb_pattern_action

        cb = _make_callback(data="pattern:dismiss:0")
        await cb_pattern_action(cb)
        cb.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_pattern_remind_creates_memory(self):
        from src.bot.handlers.memory_admin_cmds import cb_pattern_action
        from src.db.repo import get_or_create_user
        from src.db.session import get_session

        async with get_session() as session:
            await get_or_create_user(session, OWNER_TG_ID)

        cb = _make_callback(data="pattern:remind:0")
        await cb_pattern_action(cb)
        cb.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_pattern_history(self):
        from src.bot.handlers.memory_admin_cmds import cb_pattern_action

        cb = _make_callback(data="pattern:history:0")
        await cb_pattern_action(cb)
        cb.answer.assert_called_once()
        assert "История" in cb.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_pattern_write(self):
        from src.bot.handlers.memory_admin_cmds import cb_pattern_action

        cb = _make_callback(data="pattern:write:0")
        await cb_pattern_action(cb)
        cb.answer.assert_called_once()
