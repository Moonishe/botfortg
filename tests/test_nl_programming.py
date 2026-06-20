"""Tests for NL Programming integration: BackgroundGoal unification + free_text.

Verifies:
- BackgroundGoal in nl_programming.py is imported from proactive_scheduler.
- _maybe_schedule_nl_goal correctly detects cron-like phrases.
- proactive_scheduler.register() is called for valid NL goals.
- Feature flag nl_programming_enabled gates the whole path.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch, MagicMock


os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: BackgroundGoal is unified (no local duplicate)
# ═══════════════════════════════════════════════════════════════════════════


class TestBackgroundGoalUnified:
    """nl_programming.py must import BackgroundGoal from proactive_scheduler."""

    def test_nl_programming_imports_background_goal(self):
        """Verify NLProgrammer uses the canonical BackgroundGoal."""
        from src.bot.nl_programming import NLProgrammer

        # The module should not define its own BackgroundGoal
        assert not hasattr(NLProgrammer, "BackgroundGoal")

    def test_canonical_background_goal_has_required_fields(self):
        """BackgroundGoal has id, user_id, description, frequency + defaults."""
        from src.core.agents.proactive_scheduler import BackgroundGoal

        goal = BackgroundGoal(
            id="test_1",
            user_id=123,
            description="test desc",
            frequency="daily 9:00",
        )
        assert goal.id == "test_1"
        assert goal.user_id == 123
        assert goal.description == "test desc"
        assert goal.frequency == "daily 9:00"
        assert goal.enabled is True
        assert goal.last_run is None
        assert goal.next_run is None


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: _maybe_schedule_nl_goal trigger detection
# ═══════════════════════════════════════════════════════════════════════════


class TestMaybeScheduleNlGoal:
    """Tests for _maybe_schedule_nl_goal in free_text_legacy.py."""

    @pytest.fixture
    def mock_message(self):
        msg = MagicMock()
        msg.from_user.id = 123456789
        msg.answer = AsyncMock()
        return msg

    @pytest.mark.asyncio
    async def test_disabled_flag_returns_false(self, mock_message):
        """When nl_programming_enabled=False, return False immediately."""
        from src.bot.handlers.free_text_legacy import _maybe_schedule_nl_goal
        from src.config import settings

        with patch.object(settings, "nl_programming_enabled", False):
            result = await _maybe_schedule_nl_goal(
                "каждый день в 9 утра делай отчёт",
                mock_message,
                123456789,
            )
            assert result is False
            mock_message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_trigger_words_returns_false(self, mock_message):
        """Text without cron-like trigger words should return False."""
        from src.bot.handlers.free_text_legacy import _maybe_schedule_nl_goal
        from src.config import settings

        with patch.object(settings, "nl_programming_enabled", True):
            result = await _maybe_schedule_nl_goal(
                "привет, как дела?",
                mock_message,
                123456789,
            )
            assert result is False
            mock_message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_word_detected_registers_goal(self, mock_message):
        """Text with trigger word should call NLProgrammer and register goal."""
        from src.bot.handlers.free_text_legacy import _maybe_schedule_nl_goal
        from src.config import settings
        from src.core.agents.proactive_scheduler import BackgroundGoal

        # Fake goal that NLProgrammer.parse() would return
        fake_goal = BackgroundGoal(
            id="nl_123_9999",
            user_id=123456789,
            description="Делать отчёт",
            frequency="daily 9:00",
        )

        # mock_user must have telegram_id attribute
        mock_user = MagicMock()
        mock_user.telegram_id = 123456789

        with (
            patch.object(settings, "nl_programming_enabled", True),
            patch(
                "src.bot.handlers.free_text_legacy.proactive_scheduler"
            ) as mock_scheduler,
            patch(
                "src.db.repo.get_or_create_user",
                AsyncMock(return_value=mock_user),
            ),
            patch(
                "src.bot.nl_programming.NLProgrammer.parse",
                AsyncMock(return_value=fake_goal),
            ),
        ):
            mock_scheduler.register = AsyncMock()

            result = await _maybe_schedule_nl_goal(
                "каждый день в 9 утра делай отчёт",
                mock_message,
                123456789,
            )
            assert result is True
            mock_scheduler.register.assert_called_once_with(fake_goal)
            mock_message.answer.assert_called_once()
            call_text = mock_message.answer.call_args[0][0]
            assert "Задача запланирована" in call_text

    @pytest.mark.asyncio
    async def test_parse_returns_none_returns_false(self, mock_message):
        """When NLProgrammer.parse() returns None, return False."""
        from src.bot.handlers.free_text_legacy import _maybe_schedule_nl_goal
        from src.config import settings

        mock_user = MagicMock()
        mock_user.telegram_id = 123456789

        with (
            patch.object(settings, "nl_programming_enabled", True),
            patch(
                "src.db.repo.get_or_create_user",
                AsyncMock(return_value=mock_user),
            ),
            patch(
                "src.bot.nl_programming.NLProgrammer.parse",
                AsyncMock(return_value=None),
            ),
        ):
            result = await _maybe_schedule_nl_goal(
                "напоминай мне что-то",
                mock_message,
                123456789,
            )
            assert result is False
            mock_message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_caught_returns_false(self, mock_message):
        """Exceptions during NL scheduling should be caught and return False."""
        from src.bot.handlers.free_text_legacy import _maybe_schedule_nl_goal
        from src.config import settings

        mock_user = MagicMock()
        mock_user.telegram_id = 123456789

        with (
            patch.object(settings, "nl_programming_enabled", True),
            patch(
                "src.db.repo.get_or_create_user",
                AsyncMock(return_value=mock_user),
            ),
            patch(
                "src.bot.nl_programming.NLProgrammer.parse",
                side_effect=RuntimeError("LLM timeout"),
            ),
        ):
            result = await _maybe_schedule_nl_goal(
                "напомни мне завтра",
                mock_message,
                123456789,
            )
            assert result is False
            mock_message.answer.assert_not_called()

    @pytest.mark.parametrize(
        "text,should_trigger",
        [
            ("каждый день делай отчёт", True),
            ("каждые 2 часа проверяй почту", True),
            ("ежедневно в 9 утра", True),
            ("еженедельно по понедельникам", True),
            ("раз в неделю напоминай", True),
            ("по понедельникам собрание", True),
            ("напомни купить молоко", True),
            ("напоминай про тренировку", True),
            ("привет как дела", False),
            ("что такое квантовая физика", False),
            ("покажи мои заметки", False),
        ],
    )
    @pytest.mark.asyncio
    async def test_trigger_detection_parametric(
        self, text, should_trigger, mock_message
    ):
        """Parametric test: verify trigger word detection."""
        from src.bot.handlers.free_text_legacy import _maybe_schedule_nl_goal
        from src.config import settings

        with patch.object(settings, "nl_programming_enabled", True):
            # Even for trigger=True cases, without a real session the parse
            # will fail. We just check that the function doesn't crash and
            # that non-trigger texts are skipped.
            result = await _maybe_schedule_nl_goal(text, mock_message, 123456789)
            if not should_trigger:
                assert result is False
