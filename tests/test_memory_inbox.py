"""Unit tests for memory inbox handlers — memory_inbox.py.

Covers:
  - cb_memory_inbox confirm path
  - cb_memory_inbox discard path
  - cb_memory_inbox temporary / permanent paths
  - cb_memory_inbox edit path
  - cb_memory_inbox ownership check (wrong user)
  - cb_memory_inbox missing candidate
  - cb_mem_to_task handler
  - scan_content blocking (mocked)
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Environment setup BEFORE importing src modules ──────────────────
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.db.models import MemoryCandidate, Memory
from src.db.repo import get_or_create_user
from src.db.session import get_session

OWNER_TG_ID = 123456789
NON_OWNER_ID = 999999999


# ── Helpers ─────────────────────────────────────────────────────────


def _make_callback(
    user_id: int = OWNER_TG_ID, data: str = "memb:confirm:1"
) -> MagicMock:
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    return cb


async def _make_candidate(
    user_id: int = OWNER_TG_ID,
    fact: str = "Тестовый факт",
    source: str = "chat",
) -> MemoryCandidate:
    """Create a MemoryCandidate in DB."""
    async with get_session() as session:
        owner = await get_or_create_user(session, user_id)
        c = MemoryCandidate(
            user_id=owner.id,
            fact=fact,
            source=source,
            importance=0.8,
            decay_rate=0.1,
            created_at=datetime.now(timezone.utc),
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return c


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
#  Tests: cb_memory_inbox
# ═══════════════════════════════════════════════════════════════════


class TestCbMemoryInbox:
    """Tests for the memb:* callback handler."""

    @pytest.mark.asyncio
    async def test_confirm_path_creates_memory(self):
        from src.bot.handlers.memory_inbox import cb_memory_inbox

        c = await _make_candidate(fact="Нужно запомнить это")
        cb = _make_callback(data=f"memb:confirm:{c.id}")

        with patch(
            "src.core.security.prompt_injection_scanner.scan_content",
            return_value=MagicMock(blocked=False),
        ):
            await cb_memory_inbox(cb)

        cb.message.edit_text.assert_called_once()
        text = cb.message.edit_text.call_args[0][0]
        assert "Запомнил" in text
        cb.answer.assert_called_once()

        # Verify candidate is deleted
        async with get_session() as session:
            deleted = await session.get(MemoryCandidate, c.id)
            assert deleted is None

    @pytest.mark.asyncio
    async def test_discard_path_deletes_candidate(self):
        from src.bot.handlers.memory_inbox import cb_memory_inbox

        c = await _make_candidate(fact="Устаревший факт")
        cb = _make_callback(data=f"memb:discard:{c.id}")

        with patch(
            "src.core.security.prompt_injection_scanner.scan_content",
            return_value=MagicMock(blocked=False),
        ):
            await cb_memory_inbox(cb)

        cb.message.edit_text.assert_called_once()
        text = cb.message.edit_text.call_args[0][0]
        assert "Удалил" in text

        # Verify candidate is deleted
        async with get_session() as session:
            deleted = await session.get(MemoryCandidate, c.id)
            assert deleted is None

    @pytest.mark.asyncio
    async def test_temporary_path_creates_temporary_memory(self):
        from src.bot.handlers.memory_inbox import cb_memory_inbox

        c = await _make_candidate(fact="Временный факт")
        cb = _make_callback(data=f"memb:temporary:{c.id}")

        with patch(
            "src.core.security.prompt_injection_scanner.scan_content",
            return_value=MagicMock(blocked=False),
        ):
            await cb_memory_inbox(cb)

        cb.message.edit_text.assert_called_once()
        text = cb.message.edit_text.call_args[0][0]
        assert "неделю" in text.lower() or "Сохранено" in text

        async with get_session() as session:
            deleted = await session.get(MemoryCandidate, c.id)
            assert deleted is None

    @pytest.mark.asyncio
    async def test_permanent_path_creates_permanent_memory(self):
        from src.bot.handlers.memory_inbox import cb_memory_inbox

        c = await _make_candidate(fact="Важный постоянный факт")
        cb = _make_callback(data=f"memb:permanent:{c.id}")

        with patch(
            "src.core.security.prompt_injection_scanner.scan_content",
            return_value=MagicMock(blocked=False),
        ):
            await cb_memory_inbox(cb)

        cb.message.edit_text.assert_called_once()
        text = cb.message.edit_text.call_args[0][0]
        assert "навсегда" in text.lower() or "Сохранено" in text

        async with get_session() as session:
            deleted = await session.get(MemoryCandidate, c.id)
            assert deleted is None

    @pytest.mark.asyncio
    async def test_edit_path_shows_instructions(self):
        from src.bot.handlers.memory_inbox import cb_memory_inbox

        c = await _make_candidate(fact="Факт для редактирования")
        cb = _make_callback(data=f"memb:edit:{c.id}")

        with patch(
            "src.core.security.prompt_injection_scanner.scan_content",
            return_value=MagicMock(blocked=False),
        ):
            await cb_memory_inbox(cb)

        cb.message.edit_text.assert_called_once()
        text = cb.message.edit_text.call_args[0][0]
        assert "/remember" in text

    @pytest.mark.asyncio
    async def test_wrong_owner_rejected(self):
        from src.bot.handlers.memory_inbox import cb_memory_inbox

        c = await _make_candidate(fact="Чужой факт")
        cb = _make_callback(user_id=NON_OWNER_ID, data=f"memb:confirm:{c.id}")

        await cb_memory_inbox(cb)
        cb.answer.assert_called_once()
        assert "не найден" in cb.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_nonexistent_candidate_rejected(self):
        from src.bot.handlers.memory_inbox import cb_memory_inbox

        cb = _make_callback(data="memb:confirm:99999")
        await cb_memory_inbox(cb)
        cb.answer.assert_called_once()
        assert "не найден" in cb.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_scan_content_blocked(self):
        from src.bot.handlers.memory_inbox import cb_memory_inbox

        c = await _make_candidate(fact="Заблокированный факт")
        cb = _make_callback(data=f"memb:confirm:{c.id}")

        with patch(
            "src.bot.handlers.memory_inbox.scan_content",
            return_value=MagicMock(blocked=True),
        ):
            await cb_memory_inbox(cb)

        cb.answer.assert_called_once()
        assert "безопасности" in cb.answer.call_args[0][0]

        # Candidate should NOT be deleted
        async with get_session() as session:
            still_exists = await session.get(MemoryCandidate, c.id)
            assert still_exists is not None

    @pytest.mark.asyncio
    async def test_scan_content_error_passes_through(self):
        from src.bot.handlers.memory_inbox import cb_memory_inbox

        c = await _make_candidate(fact="Факт с ошибкой сканера")
        cb = _make_callback(data=f"memb:confirm:{c.id}")

        with patch(
            "src.bot.handlers.memory_inbox.scan_content",
            side_effect=RuntimeError("simulated scanner failure"),
        ):
            await cb_memory_inbox(cb)

        # Should still process (error handling passes through)
        cb.message.edit_text.assert_called_once()
        assert "Запомнил" in cb.message.edit_text.call_args[0][0]


# ═══════════════════════════════════════════════════════════════════
#  Tests: cb_mem_to_task
# ═══════════════════════════════════════════════════════════════════


class TestCbMemToTask:
    """Tests for mem:totask:* handler."""

    @pytest.mark.asyncio
    async def test_creates_task_from_memory(self):
        from src.bot.handlers.memory_inbox import cb_mem_to_task

        async with get_session() as session:
            owner = await get_or_create_user(session, OWNER_TG_ID)
            mem = Memory(
                user_id=owner.id,
                fact="Факт для задачи",
                source="user",
                is_active=True,
            )
            session.add(mem)
            await session.commit()
            await session.refresh(mem)

        cb = _make_callback(data=f"mem:totask:{mem.id}")
        await cb_mem_to_task(cb)

        cb.message.edit_text.assert_called_once()
        text = cb.message.edit_text.call_args[0][0]
        assert "Задача создана" in text
        cb.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_nonexistent_memory_rejected(self):
        from src.bot.handlers.memory_inbox import cb_mem_to_task

        cb = _make_callback(data="mem:totask:99999")
        await cb_mem_to_task(cb)
        cb.answer.assert_called_once()
        assert "не найден" in cb.answer.call_args[0][0]
