"""Smoke tests for free_text_pipeline.py — pure function coverage.

Verifies:
- Module can be imported without errors (env-vars set first).
- Pure functions (_time_of_day_greeting, _detect_context_hint,
  _looks_like_send_request, _safe_for_deep_humanize) behave correctly.
- Heavy tests (LLM, DB, async dispatch) are intentionally deferred to
  test_free_text_dispatch.py and test_free_text_exec.py.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")

import pytest


# ===========================================================================
# TestModuleImport
# ===========================================================================


class TestModuleImport:
    """The pipeline module must be importable without side-effects."""

    def test_import_pipeline_succeeds(self):
        """free_text_pipeline.py imports cleanly when env vars are set."""
        from src.bot.handlers.free_text_pipeline import (
            CLASSIC_INTENT_HANDLERS,
            INTENT_HANDLERS,
        )

        assert isinstance(INTENT_HANDLERS, dict)
        assert isinstance(CLASSIC_INTENT_HANDLERS, dict)

    def test_intent_registries_not_empty(self):
        """INTENT_HANDLERS and CLASSIC_INTENT_HANDLERS have entries."""
        from src.bot.handlers.free_text_pipeline import (
            CLASSIC_INTENT_HANDLERS,
            INTENT_HANDLERS,
        )

        assert len(INTENT_HANDLERS) > 0, "INTENT_HANDLERS is empty"
        assert len(CLASSIC_INTENT_HANDLERS) > 0, "CLASSIC_INTENT_HANDLERS is empty"


# ===========================================================================
# TestTimeOfDayGreeting
# ===========================================================================


class TestTimeOfDayGreeting:
    """_time_of_day_greeting() — pure function, no external deps."""

    @staticmethod
    def _make_dt(hour: int):
        """Return a mock datetime with the given hour."""
        from datetime import datetime

        return datetime(2025, 1, 15, hour, 0, 0)

    def test_morning(self):
        from src.bot.handlers.free_text_pipeline import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text_pipeline.now_in_tz",
            return_value=self._make_dt(7),
        ):
            assert _time_of_day_greeting() == "Доброе утро"

    def test_afternoon(self):
        from src.bot.handlers.free_text_pipeline import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text_pipeline.now_in_tz",
            return_value=self._make_dt(14),
        ):
            assert _time_of_day_greeting() == "Добрый день"

    def test_evening(self):
        from src.bot.handlers.free_text_pipeline import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text_pipeline.now_in_tz",
            return_value=self._make_dt(19),
        ):
            assert _time_of_day_greeting() == "Добрый вечер"

    def test_night(self):
        from src.bot.handlers.free_text_pipeline import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text_pipeline.now_in_tz",
            return_value=self._make_dt(2),
        ):
            assert _time_of_day_greeting() == "Доброй ночи"

    def test_boundary_morning_start(self):
        from src.bot.handlers.free_text_pipeline import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text_pipeline.now_in_tz",
            return_value=self._make_dt(6),
        ):
            assert _time_of_day_greeting() == "Доброе утро"

    def test_boundary_afternoon_start(self):
        from src.bot.handlers.free_text_pipeline import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text_pipeline.now_in_tz",
            return_value=self._make_dt(12),
        ):
            assert _time_of_day_greeting() == "Добрый день"

    def test_boundary_evening_start(self):
        from src.bot.handlers.free_text_pipeline import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text_pipeline.now_in_tz",
            return_value=self._make_dt(18),
        ):
            assert _time_of_day_greeting() == "Добрый вечер"

    def test_boundary_night_start(self):
        from src.bot.handlers.free_text_pipeline import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text_pipeline.now_in_tz",
            return_value=self._make_dt(23),
        ):
            assert _time_of_day_greeting() == "Доброй ночи"


# ===========================================================================
# TestDetectContextHint
# ===========================================================================


class TestDetectContextHint:
    """_detect_context_hint() — pure function, keyword-based classification."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("найди контакт в чатах", "search"),
            ("поищи сообщения про встречу", "search"),
            ("проанализируй логи", "analysis"),
            ("разбери эту тему", "analysis"),
            ("напиши оле привет", "send"),
            ("отправь ему сообщение", "send"),
            ("напомни через час позвонить", "reminder"),
            ("запомни что сегодня понедельник", "memory"),
            ("сохрани в память важное", "memory"),
            ("новости за сегодня", "news"),
            ("дайджест чатов", "news"),
            ("как дела вообще", None),
            ("", None),
        ],
    )
    def test_keyword_hints(self, text, expected):
        from src.bot.handlers.free_text_pipeline import _detect_context_hint

        assert _detect_context_hint(text) == expected

    def test_purpose_overrides_keywords(self):
        """plan_purpose has priority over keyword detection."""
        from src.bot.handlers.free_text_pipeline import _detect_context_hint

        # Text suggests "search", but purpose says "analysis"
        result = _detect_context_hint("найди и проанализируй", plan_purpose="analysis")
        assert result == "analysis"

    def test_unknown_purpose_falls_back_to_keyword(self):
        from src.bot.handlers.free_text_pipeline import _detect_context_hint

        result = _detect_context_hint(
            "найди мне что-нибудь", plan_purpose="unknown_task"
        )
        assert result == "search"


# ===========================================================================
# TestLooksLikeSendRequest
# ===========================================================================


class TestLooksLikeSendRequest:
    """_looks_like_send_request() — pure function."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("напиши оле что я скоро буду", True),
            ("напиши ему привет", True),
            ("напиши маме спасибо", True),
            ("отправь сообщение коллеге", True),
            ("напиши код на python", False),
            ("напиши рецепт борща", False),
            ("напиши стихотворение", False),
            ("привет как дела", False),
        ],
    )
    def test_send_detection(self, text, expected):
        from src.bot.handlers.free_text_pipeline import _looks_like_send_request

        assert _looks_like_send_request(text) == expected


# ===========================================================================
# TestSafeForDeepHumanize
# ===========================================================================


class TestSafeForDeepHumanize:
    """_safe_for_deep_humanize() — pure function, guards structured output."""

    @pytest.mark.parametrize(
        "text, hint, expected",
        [
            ("Привет, как дела?", None, True),
            ("Вот результат анализа", None, True),
            ("```python\nprint(1)\n```", None, False),
            ('{"key": "value"}', None, False),
            ("- пункт 1\n- пункт 2", None, False),
            ("1. первый шаг", None, False),
            ("Вот SQL запрос для базы", None, False),
            ("любой текст для отправки", "send", False),
            ("", None, False),
            ("col1|col2\nval1|val2", None, False),
        ],
    )
    def test_humanize_guard(self, text, hint, expected):
        from src.bot.handlers.free_text_pipeline import _safe_for_deep_humanize

        assert _safe_for_deep_humanize(text, context_hint=hint) == expected
