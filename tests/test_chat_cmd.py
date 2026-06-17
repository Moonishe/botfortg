"""Unit tests for chat command handlers — chat_cmd.py.

Covers:
  - _parse_peer_id (valid 3-segment, valid 4-segment, invalid formats)
  - Callback handlers (cb_pick, cb_watch, cb_summary, cb_tasks, cb_draft,
    cb_catchup, cb_limit, cb_unwatch) — rejection of malformed callback_data
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Environment setup BEFORE importing src modules ──────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"  # noqa: S105
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.bot.handlers.chat_cmd import (
    _parse_peer_id,
    cb_pick,
    cb_watch,
    cb_unwatch,
    cb_summary,
    cb_tasks,
    cb_draft,
    cb_catchup,
    cb_limit,
)

OWNER_TG_ID = 123456789


# ── Helpers ─────────────────────────────────────────────────────────


def _make_callback(data: str) -> MagicMock:
    """Create a mock aiogram CallbackQuery with given callback_data."""
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = OWNER_TG_ID
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    return cb


# ═══════════════════════════════════════════════════════════════════
#  Tests: _parse_peer_id
# ═══════════════════════════════════════════════════════════════════


class TestParsePeerId:
    """Direct unit tests for _parse_peer_id — no DB, no mocks needed."""

    # ── valid 3-segment ──

    def test_parse_3seg_pick(self):
        assert _parse_peer_id("chat:pick:123") == 123

    def test_parse_3seg_watch(self):
        assert _parse_peer_id("chat:watch:456") == 456

    def test_parse_3seg_limit(self):
        assert _parse_peer_id("chat:limit:789") == 789

    # ── valid 4-segment ──

    def test_parse_4seg_summary(self):
        assert _parse_peer_id("chat:summary:123:50") == 123

    def test_parse_4seg_tasks(self):
        assert _parse_peer_id("chat:tasks:456:50") == 456

    def test_parse_4seg_draft(self):
        assert _parse_peer_id("chat:draft:789:50") == 789

    def test_parse_4seg_catchup(self):
        assert _parse_peer_id("chat:catchup:101:50") == 101

    # ── invalid: too few segments ──

    def test_parse_too_few_segments(self):
        assert _parse_peer_id("chat:pick") is None

    def test_parse_empty_string(self):
        assert _parse_peer_id("") is None

    # ── invalid: non-int peer segment ──

    def test_parse_nonint_peer_3seg(self):
        assert _parse_peer_id("chat:pick:abc") is None

    def test_parse_nonint_peer_4seg(self):
        assert _parse_peer_id("chat:summary:abc:50") is None

    # ── invalid: empty peer segment ──

    def test_parse_empty_peer_3seg(self):
        assert _parse_peer_id("chat:pick:") is None

    # ── edge: 5+ segments still return peer from position 2 ──

    def test_parse_5seg_returns_peer(self):
        assert _parse_peer_id("chat:summary:123:50:extra") == 123


# ═══════════════════════════════════════════════════════════════════
#  Tests: Callback handlers reject malformed data
# ═══════════════════════════════════════════════════════════════════


class TestCallbackRejectsMalformed:
    """Each callback handler must answer with "Ошибка данных." on bad data."""

    @pytest.mark.asyncio
    async def test_cb_pick_rejects_nonint(self):
        cb = _make_callback("chat:pick:abc")
        await cb_pick(cb, userbot_manager=MagicMock())
        cb.answer.assert_called_once_with("Ошибка данных.", show_alert=True)

    @pytest.mark.asyncio
    async def test_cb_watch_rejects_empty_peer(self):
        cb = _make_callback("chat:watch:")
        await cb_watch(cb)
        cb.answer.assert_called_once_with("Ошибка данных.", show_alert=True)

    @pytest.mark.asyncio
    async def test_cb_summary_rejects_nonint(self):
        cb = _make_callback("chat:summary:xyz:50")
        await cb_summary(cb, userbot_manager=MagicMock())
        cb.answer.assert_called_once_with("Ошибка данных.", show_alert=True)

    @pytest.mark.asyncio
    async def test_cb_limit_rejects_empty_peer(self):
        cb = _make_callback("chat:limit:")
        await cb_limit(cb)
        cb.answer.assert_called_once_with("Ошибка данных.", show_alert=True)

    @pytest.mark.asyncio
    async def test_cb_unwatch_rejects_nonint(self):
        cb = _make_callback("chat:unwatch:bad")
        await cb_unwatch(cb)
        cb.answer.assert_called_once_with("Ошибка данных.", show_alert=True)

    @pytest.mark.asyncio
    async def test_cb_tasks_rejects_empty_peer(self):
        cb = _make_callback("chat:tasks:")
        await cb_tasks(cb, userbot_manager=MagicMock())
        cb.answer.assert_called_once_with("Ошибка данных.", show_alert=True)

    @pytest.mark.asyncio
    async def test_cb_draft_rejects_empty_peer(self):
        cb = _make_callback("chat:draft:")
        await cb_draft(cb, userbot_manager=MagicMock())
        cb.answer.assert_called_once_with("Ошибка данных.", show_alert=True)

    @pytest.mark.asyncio
    async def test_cb_catchup_rejects_empty_peer(self):
        cb = _make_callback("chat:catchup:")
        await cb_catchup(cb, userbot_manager=MagicMock())
        cb.answer.assert_called_once_with("Ошибка данных.", show_alert=True)
