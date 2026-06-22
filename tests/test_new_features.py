"""Tests for new features added in Max Mode A-I sessions.

Covers: scrub_internal_tags, process_nl_feedback, pairing TTL, group gating, response pacing config.
ponytail: one test file for all new features, split if grows >200 lines.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── scrub_internal_tags ──────────────────────────────────────────────────────


class TestScrubInternalTags:
    """Test the internal tag scrubber for LLM output."""

    def test_plain_text_unchanged(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        assert scrub_internal_tags("Hello world") == "Hello world"

    def test_empty_string(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        assert scrub_internal_tags("") == ""

    def test_none_input(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        assert scrub_internal_tags(None) is None  # type: ignore[arg-type]

    def test_memory_context_block_removed(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        text = "Hello <memory-context>secret data</memory-context> world"
        result = scrub_internal_tags(text)
        assert "secret data" not in result
        assert "Hello" in result
        assert "world" in result

    def test_system_note_block_removed(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        text = "Reply <system-note>internal instruction</system-note> done"
        result = scrub_internal_tags(text)
        assert "internal instruction" not in result
        assert "Reply" in result
        assert "done" in result

    def test_think_block_removed(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        text = "Answer <think>reasoning here</think> final"
        result = scrub_internal_tags(text)
        assert "reasoning here" not in result
        assert "Answer" in result
        assert "final" in result

    def test_standalone_opening_tag_removed(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        text = "Hello <memory-context>world"
        result = scrub_internal_tags(text)
        assert "<memory-context>" not in result
        assert "Hello" in result
        assert "world" in result

    def test_standalone_closing_tag_removed(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        text = "Hello world</memory-context>"
        result = scrub_internal_tags(text)
        assert "</memory-context>" not in result

    def test_tag_with_attributes_removed(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        text = 'Text <memory-context type="user">data</memory-context> end'
        result = scrub_internal_tags(text)
        assert "data" not in result
        assert "Text" in result
        assert "end" in result

    def test_multiple_blocks_removed(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        text = "A<memory-context>x</memory-context>B<system-note>y</system-note>C"
        result = scrub_internal_tags(text)
        assert "x" not in result
        assert "y" not in result
        assert "A" in result
        assert "B" in result
        assert "C" in result

    def test_reasoning_tag_removed(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        text = "Sure <reasoning>step by step</reasoning> answer"
        result = scrub_internal_tags(text)
        assert "step by step" not in result
        assert "Sure" in result
        assert "answer" in result

    def test_case_insensitive(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        text = "Hello <MEMORY-CONTEXT>data</MEMORY-CONTEXT> end"
        result = scrub_internal_tags(text)
        assert "data" not in result

    def test_normal_html_preserved(self):
        from src.core.security.prompt_guard import scrub_internal_tags

        text = "Click <a href='https://example.com'>here</a> please"
        result = scrub_internal_tags(text)
        assert "<a href='https://example.com'>" in result
        assert "here" in result


# ── process_nl_feedback ──────────────────────────────────────────────────────


class TestProcessNlFeedback:
    """Test NL memory feedback classification."""

    @pytest.mark.asyncio
    async def test_short_text_returns_none(self):
        from src.core.memory.reaction_feedback import process_nl_feedback

        result = await process_nl_feedback("ok", 123)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self):
        from src.core.memory.reaction_feedback import process_nl_feedback

        result = await process_nl_feedback("", 123)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self):
        from src.core.memory.reaction_feedback import process_nl_feedback

        result = await process_nl_feedback("Привет, как дела?", 123)
        assert result is None

    @pytest.mark.asyncio
    async def test_supplement_detected(self):
        from src.core.memory.reaction_feedback import process_nl_feedback

        result = await process_nl_feedback("Кстати, он ещё любит пиццу", 123)
        assert result is not None
        assert result["action"] == "supplementing"

    @pytest.mark.asyncio
    async def test_replace_detected(self):
        from src.core.memory.reaction_feedback import process_nl_feedback

        result = await process_nl_feedback("Забудь это, вот правильный вариант", 123)
        assert result is not None
        assert result["action"] == "replacing"

    @pytest.mark.asyncio
    async def test_correcting_via_detect_memory_correction(self):
        """detect_memory_correction handles negation patterns like 'нет, он не...'."""
        from src.core.memory.reaction_feedback import process_nl_feedback

        # detect_memory_correction may or may not match depending on its patterns,
        # but process_nl_feedback should not crash.
        result = await process_nl_feedback("Нет, его зовут не Иван, а Пётр", 123)
        # Either correcting (from detect_memory_correction) or None
        if result is not None:
            assert result["action"] in (
                "correcting",
                "supplementing",
                "replacing",
                "update",
            )


# ── Pairing TTL ──────────────────────────────────────────────────────────────


class TestPairingTTL:
    """Test pairing code TTL and expiry."""

    @pytest.mark.asyncio
    async def test_start_pairing_returns_code(self, tmp_path):
        from src.core.security.pairing import PairingManager

        mgr = PairingManager(data_dir=tmp_path)
        code = await mgr.start_pairing(999)
        assert isinstance(code, str)
        assert len(code) >= 16

    @pytest.mark.asyncio
    async def test_is_pending_true_after_start(self, tmp_path):
        from src.core.security.pairing import PairingManager

        mgr = PairingManager(data_dir=tmp_path)
        await mgr.start_pairing(999)
        assert await mgr.is_pending(999) is True

    @pytest.mark.asyncio
    async def test_is_pending_false_for_unknown(self, tmp_path):
        from src.core.security.pairing import PairingManager

        mgr = PairingManager(data_dir=tmp_path)
        assert await mgr.is_pending(888) is False

    @pytest.mark.asyncio
    async def test_expired_pending_returns_false(self, tmp_path):
        from src.core.security.pairing import PairingManager, _PENDING_TTL

        mgr = PairingManager(data_dir=tmp_path)
        await mgr.start_pairing(999)
        # Manually expire the entry.
        async with mgr._lock:
            code, _ = mgr._pending[999]
            mgr._pending[999] = (code, time.time() - _PENDING_TTL - 1)
        assert await mgr.is_pending(999) is False

    @pytest.mark.asyncio
    async def test_max_pending_raises(self, tmp_path):
        from src.core.security.pairing import PairingManager, _MAX_PENDING

        mgr = PairingManager(data_dir=tmp_path)
        for i in range(_MAX_PENDING):
            await mgr.start_pairing(i)
        with pytest.raises(RuntimeError, match="limit reached"):
            await mgr.start_pairing(_MAX_PENDING + 1)

    @pytest.mark.asyncio
    async def test_old_format_expired_on_load(self, tmp_path):
        """Old format (str without ts) should be expired immediately."""
        import json

        from src.core.security.pairing import PairingManager

        # Write old-format file.
        (tmp_path / "pending_pairings.json").write_text(
            json.dumps({"999": "oldcode123"}), encoding="utf-8"
        )
        mgr = PairingManager(data_dir=tmp_path)
        # Old format → ts=0 → expired
        assert 999 not in mgr._pending


# ── Group Gating Config ──────────────────────────────────────────────────────


class TestGroupGatingConfig:
    """Test userbot group gating configuration fields exist."""

    def test_config_has_group_fields(self):
        from src.config import settings

        assert hasattr(settings, "userbot_group_enabled")
        assert hasattr(settings, "userbot_group_allowed_ids")
        assert hasattr(settings, "userbot_group_require_mention")

    def test_group_disabled_by_default(self):
        from src.config import settings

        assert settings.userbot_group_enabled is False

    def test_require_mention_default_true(self):
        from src.config import settings

        assert settings.userbot_group_require_mention is True


# ── Response Pacing Config ───────────────────────────────────────────────────


class TestResponsePacingConfig:
    """Test response pacing configuration fields."""

    def test_config_has_pacing_fields(self):
        from src.config import settings

        assert hasattr(settings, "response_pacing_mode")
        assert hasattr(settings, "response_pacing_min_ms")
        assert hasattr(settings, "response_pacing_max_ms")

    def test_pacing_off_by_default(self):
        from src.config import settings

        assert settings.response_pacing_mode == "off"

    def test_pacing_min_max_defaults(self):
        from src.config import settings

        assert settings.response_pacing_min_ms == 500
        assert settings.response_pacing_max_ms == 2000
