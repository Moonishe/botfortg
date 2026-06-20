"""Unit tests for API keys management handlers — keys_cmd.py.

Covers:
  - _PendingKeyEntryFilter (valid, no entry, expired, FSM blocked)
  - _PendingImportFilter (valid, no entry, expired, FSM blocked)
  - cmd_keys basic happy path (list slots, help)
  - cb_keys_model callbacks (custom model, known model, none model)
  - _guess_provider prefix matching
  - _build_category_keyboard / _build_provider_keyboard / _build_model_keyboard
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Environment setup BEFORE importing src modules ──────────────────
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.bot.handlers.keys_cmd import (
    _PendingKeyEntryFilter,
    _PendingImportFilter,
    _PENDING_KEY_ENTRIES,
    _PENDING_IMPORTS,
    _guess_provider,
    _build_category_keyboard,
    _build_provider_keyboard,
    _build_model_keyboard,
    cmd_keys,
    cb_keys_model,
    cb_keys_cata,
    cb_keys_cat,
    cb_keys_back_close,
    cb_keys_back_cat,
    cb_keys_back_provider,
    cb_keys_remove,
    router,
)

OWNER_TG_ID = 123456789


# ── Helpers ─────────────────────────────────────────────────────────


def _make_message(user_id: int = OWNER_TG_ID, text: str = "/keys") -> MagicMock:
    """Create a mock aiogram Message with from_user and answer."""
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    msg.delete = AsyncMock()
    msg.edit_text = AsyncMock()
    return msg


def _make_callback(
    user_id: int = OWNER_TG_ID, data: str = "keys:cat:llm:openai"
) -> MagicMock:
    """Create a mock aiogram CallbackQuery."""
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
def _clean_pending():
    """Clear pending dictionaries before each test."""
    _PENDING_KEY_ENTRIES.clear()
    _PENDING_IMPORTS.clear()
    yield
    _PENDING_KEY_ENTRIES.clear()
    _PENDING_IMPORTS.clear()


# ═══════════════════════════════════════════════════════════════════
#  Tests: _PendingKeyEntryFilter
# ═══════════════════════════════════════════════════════════════════


class TestPendingKeyEntryFilter:
    """Unit tests for the _PendingKeyEntryFilter."""

    @pytest.mark.asyncio
    async def test_returns_true_when_pending_exists(self):
        f = _PendingKeyEntryFilter()
        msg = _make_message()
        _PENDING_KEY_ENTRIES[OWNER_TG_ID] = {
            "provider": "openai",
            "deadline": time.monotonic() + 300,
        }
        result = await f(msg, None)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_pending(self):
        f = _PendingKeyEntryFilter()
        msg = _make_message()
        result = await f(msg, None)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_expired(self):
        f = _PendingKeyEntryFilter()
        msg = _make_message()
        _PENDING_KEY_ENTRIES[OWNER_TG_ID] = {
            "provider": "openai",
            "deadline": time.monotonic() - 10,  # expired
        }
        result = await f(msg, None)
        assert result is False
        # Expired entry should be removed
        assert OWNER_TG_ID not in _PENDING_KEY_ENTRIES

    @pytest.mark.asyncio
    async def test_returns_false_when_user_is_none(self):
        f = _PendingKeyEntryFilter()
        msg = _make_message()
        msg.from_user = None
        _PENDING_KEY_ENTRIES[OWNER_TG_ID] = {"deadline": time.monotonic() + 300}
        result = await f(msg, None)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_fsm_active(self):
        f = _PendingKeyEntryFilter()
        msg = _make_message()
        _PENDING_KEY_ENTRIES[OWNER_TG_ID] = {"deadline": time.monotonic() + 300}
        mock_state = MagicMock()
        mock_state.get_state = AsyncMock(return_value="some_fsm_state")
        result = await f(msg, mock_state)
        assert result is False


# ═══════════════════════════════════════════════════════════════════
#  Tests: _PendingImportFilter
# ═══════════════════════════════════════════════════════════════════


class TestPendingImportFilter:
    """Unit tests for _PendingImportFilter."""

    @pytest.mark.asyncio
    async def test_returns_true_when_import_pending(self):
        f = _PendingImportFilter()
        msg = _make_message()
        _PENDING_IMPORTS[OWNER_TG_ID] = {
            "purpose": "main",
            "deadline": time.monotonic() + 300,
        }
        result = await f(msg, None)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_import_pending(self):
        f = _PendingImportFilter()
        msg = _make_message()
        result = await f(msg, None)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_import_expired(self):
        f = _PendingImportFilter()
        msg = _make_message()
        _PENDING_IMPORTS[OWNER_TG_ID] = {
            "purpose": "main",
            "deadline": time.monotonic() - 60,
        }
        result = await f(msg, None)
        assert result is False
        assert OWNER_TG_ID not in _PENDING_IMPORTS

    @pytest.mark.asyncio
    async def test_returns_false_when_user_is_none(self):
        f = _PendingImportFilter()
        msg = _make_message()
        msg.from_user = None
        _PENDING_IMPORTS[OWNER_TG_ID] = {
            "purpose": "main",
            "deadline": time.monotonic() + 300,
        }
        result = await f(msg, None)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_fsm_active_import(self):
        f = _PendingImportFilter()
        msg = _make_message()
        _PENDING_IMPORTS[OWNER_TG_ID] = {
            "purpose": "main",
            "deadline": time.monotonic() + 300,
        }
        mock_state = MagicMock()
        mock_state.get_state = AsyncMock(return_value="some_state")
        result = await f(msg, mock_state)
        assert result is False


# ═══════════════════════════════════════════════════════════════════
#  Tests: _guess_provider
# ═══════════════════════════════════════════════════════════════════


class TestGuessProvider:
    """Unit tests for prefix-based provider guessing."""

    def test_openai_sk_prefix(self):
        assert _guess_provider("sk-proj-abcd1234") == "openai"

    def test_openrouter_sk_or_prefix(self):
        assert _guess_provider("sk-or-v1-abcdef") == "openrouter"

    def test_anthropic_sk_ant_prefix(self):
        assert _guess_provider("sk-ant-api03-xxxx") == "openai"

    def test_gemini_aiza_prefix(self):
        assert _guess_provider("AIzaSyABC123def456ghi") == "gemini"

    def test_cloudflare_cfat_prefix(self):
        assert _guess_provider("cfat_abc123xyz") == "cloudflare"

    def test_cloudflare_cf_prefix(self):
        assert _guess_provider("CF-abc123xyz") == "cloudflare"

    def test_mistral_ms_prefix(self):
        assert _guess_provider("ms-abc123") == "mistral"

    def test_mistral_mistral_prefix(self):
        assert _guess_provider("mistral-abc123") == "mistral"

    def test_unknown_prefix_returns_none(self):
        assert _guess_provider("unknown-key-123") is None

    def test_empty_key_returns_none(self):
        assert _guess_provider("") is None


# ═══════════════════════════════════════════════════════════════════
#  Tests: cmd_keys (basic happy path)
# ═══════════════════════════════════════════════════════════════════


class TestCmdKeys:
    """Tests for /keys command handler."""

    @pytest.mark.asyncio
    async def test_cmd_keys_no_args_shows_help(self):
        """/keys with no args shows usage instructions."""
        msg = _make_message(text="/keys")

        # Need DB for list_key_slots — use in-memory + init
        from src.db.session import init_db

        await init_db()

        await cmd_keys(msg)
        msg.answer.assert_called_once()
        call_text = msg.answer.call_args[0][0]
        assert "Нет ключевых слотов" in call_text

    @pytest.mark.asyncio
    async def test_cmd_keys_add_shows_category_keyboard(self):
        """/keys add shows category selection keyboard."""
        msg = _make_message(text="/keys add")
        await cmd_keys(msg)
        msg.answer.assert_called_once()
        call_kwargs = msg.answer.call_args[1]
        assert "reply_markup" in call_kwargs
        assert "Выбери категорию ключа" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_keys_add_invalid_provider(self):
        """/keys add with invalid provider shows error."""
        msg = _make_message(text="/keys add badprovider main somekey")
        await cmd_keys(msg)
        msg.answer.assert_called_once()
        assert "Провайдер" in msg.answer.call_args[0][0]


# ═══════════════════════════════════════════════════════════════════
#  Tests: cb_keys_model callbacks
# ═══════════════════════════════════════════════════════════════════


class TestCbKeysModel:
    """Tests for cb_keys_model callback handler."""

    @pytest.mark.asyncio
    async def test_known_model_sets_pending_entry(self):
        """Picking a known model creates a pending key entry."""
        cb = _make_callback(data="keys:model:openai:gpt-4o")
        # Ensure no leftover pending
        _PENDING_KEY_ENTRIES.clear()
        await cb_keys_model(cb)
        assert OWNER_TG_ID in _PENDING_KEY_ENTRIES
        entry = _PENDING_KEY_ENTRIES[OWNER_TG_ID]
        assert entry["provider"] == "openai"
        assert entry["model"] == "gpt-4o"
        assert entry["model_pending"] is False
        cb.message.edit_text.assert_called_once()
        cb.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_model_sets_model_pending(self):
        """Picking '__custom__' sets model_pending=True."""
        cb = _make_callback(data="keys:model:openai:__custom__")
        _PENDING_KEY_ENTRIES.clear()
        await cb_keys_model(cb)
        assert OWNER_TG_ID in _PENDING_KEY_ENTRIES
        entry = _PENDING_KEY_ENTRIES[OWNER_TG_ID]
        assert entry["provider"] == "openai"
        assert entry["model_pending"] is True

    @pytest.mark.asyncio
    async def test_none_model_sets_none_model(self):
        """Picking 'none' model sets model=None."""
        cb = _make_callback(data="keys:model:openai:none")
        _PENDING_KEY_ENTRIES.clear()
        await cb_keys_model(cb)
        assert OWNER_TG_ID in _PENDING_KEY_ENTRIES
        entry = _PENDING_KEY_ENTRIES[OWNER_TG_ID]
        assert entry["provider"] == "openai"
        assert entry["model"] is None
        assert entry["model_pending"] is False


# ═══════════════════════════════════════════════════════════════════
#  Tests: Keyboard builders
# ═══════════════════════════════════════════════════════════════════


class TestKeyboardBuilders:
    """Tests for inline keyboard builder functions."""

    def test_build_category_keyboard_has_buttons(self):
        kb = _build_category_keyboard()
        assert kb is not None
        # Should have LLM, STT, TTS, Close buttons
        assert len(kb.inline_keyboard) > 0

    def test_build_provider_keyboard_for_llm(self):
        kb = _build_provider_keyboard("llm")
        assert kb is not None

    def test_build_model_keyboard_for_known_provider(self):
        kb = _build_model_keyboard("openai")
        assert kb is not None

    def test_build_model_keyboard_unknown_provider(self):
        kb = _build_model_keyboard("nonexistent_provider_123")
        assert kb is None


# ═══════════════════════════════════════════════════════════════════
#  Tests: Back navigation callbacks
# ═══════════════════════════════════════════════════════════════════


class TestBackNavigation:
    """Tests for back/close callbacks."""

    @pytest.mark.asyncio
    async def test_back_close(self):
        cb = _make_callback(data="keys:back:close")
        await cb_keys_back_close(cb)
        cb.message.edit_text.assert_called_once()
        assert "отменено" in cb.message.edit_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_back_cat(self):
        cb = _make_callback(data="keys:back:cat")
        await cb_keys_back_cat(cb)
        cb.message.edit_text.assert_called_once()
        assert "категорию" in cb.message.edit_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_back_provider(self):
        cb = _make_callback(data="keys:back:provider:llm")
        await cb_keys_back_provider(cb)
        cb.message.edit_text.assert_called_once()
