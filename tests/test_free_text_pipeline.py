"""Smoke tests for free_text.py — pure function coverage.

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
        """free_text.py imports cleanly when env vars are set."""
        from src.bot.handlers.free_text import (
            CLASSIC_INTENT_HANDLERS,
            INTENT_HANDLERS,
        )

        assert isinstance(INTENT_HANDLERS, dict)
        assert isinstance(CLASSIC_INTENT_HANDLERS, dict)

    def test_intent_registries_not_empty(self):
        """INTENT_HANDLERS and CLASSIC_INTENT_HANDLERS have entries."""
        from src.bot.handlers.free_text import (
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
        from src.bot.handlers.free_text import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text._core.now_in_tz",
            return_value=self._make_dt(7),
        ):
            assert _time_of_day_greeting() == "Доброе утро"

    def test_afternoon(self):
        from src.bot.handlers.free_text import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text._core.now_in_tz",
            return_value=self._make_dt(14),
        ):
            assert _time_of_day_greeting() == "Добрый день"

    def test_evening(self):
        from src.bot.handlers.free_text import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text._core.now_in_tz",
            return_value=self._make_dt(19),
        ):
            assert _time_of_day_greeting() == "Добрый вечер"

    def test_night(self):
        from src.bot.handlers.free_text import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text._core.now_in_tz",
            return_value=self._make_dt(2),
        ):
            assert _time_of_day_greeting() == "Доброй ночи"

    def test_boundary_morning_start(self):
        from src.bot.handlers.free_text import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text._core.now_in_tz",
            return_value=self._make_dt(6),
        ):
            assert _time_of_day_greeting() == "Доброе утро"

    def test_boundary_afternoon_start(self):
        from src.bot.handlers.free_text import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text._core.now_in_tz",
            return_value=self._make_dt(12),
        ):
            assert _time_of_day_greeting() == "Добрый день"

    def test_boundary_evening_start(self):
        from src.bot.handlers.free_text import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text._core.now_in_tz",
            return_value=self._make_dt(18),
        ):
            assert _time_of_day_greeting() == "Добрый вечер"

    def test_boundary_night_start(self):
        from src.bot.handlers.free_text import _time_of_day_greeting

        with patch(
            "src.bot.handlers.free_text._core.now_in_tz",
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
        from src.bot.handlers.free_text import _detect_context_hint

        assert _detect_context_hint(text) == expected

    def test_purpose_overrides_keywords(self):
        """plan_purpose has priority over keyword detection."""
        from src.bot.handlers.free_text import _detect_context_hint

        # Text suggests "search", but purpose says "analysis"
        result = _detect_context_hint("найди и проанализируй", plan_purpose="analysis")
        assert result == "analysis"

    def test_unknown_purpose_falls_back_to_keyword(self):
        from src.bot.handlers.free_text import _detect_context_hint

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
        from src.bot.handlers.free_text import _looks_like_send_request

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
        from src.bot.handlers.free_text import _safe_for_deep_humanize

        assert _safe_for_deep_humanize(text, context_hint=hint) == expected


# ===========================================================================
# TestExecuteInstant — regression tests for Bug 1 & Bug 2 fixes
# ===========================================================================


class TestExecuteInstant:
    """Regression tests for execute_instant() correctness bugs.

    Bug 1: Double message when _via_dispatcher=True + route_cache_hit=True.
    Bug 2: Trajectory/optimizer records plan.final_response instead of actual response.
    """

    @pytest.fixture
    def _mock_plan(self):
        """Create a mock RouterPlan with metrics and tasks."""
        from unittest.mock import MagicMock

        plan = MagicMock()
        plan.metrics = {}
        plan.final_response = "Это тестовый ответ от LLM"
        plan.tasks = [MagicMock()]
        plan.tasks[0].purpose = MagicMock()
        plan.tasks[0].purpose.value = "main"
        return plan

    @pytest.fixture
    def _mock_msg(self):
        from unittest.mock import AsyncMock, MagicMock

        msg = MagicMock()
        msg.from_user.id = 123456789
        msg.chat.id = 123456789
        msg.answer = AsyncMock()
        return msg

    def _make_fake_task(self, coro):
        """Wrap coroutine in a mock that quacks like asyncio.Task for track_ff."""
        from unittest.mock import MagicMock

        t = MagicMock()
        t.done.return_value = True
        return t

    # ── Bug 1 tests ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cache_hit_via_dispatcher_sends_once(self, _mock_plan, _mock_msg):
        """Bug 1: _via_dispatcher=True + cache hit → safe_answer once, returns True."""
        from unittest.mock import AsyncMock, patch
        import asyncio as _asyncio_mod

        _mock_plan.metrics["route_cache_hit"] = True

        with (
            patch(
                "src.bot.handlers.free_text._core.check_pre_gate",
                return_value=None,
            ),
            patch(
                "src.bot.handlers.free_text._core._get_anti_ai_mode",
                new_callable=AsyncMock,
                return_value="normal",
            ),
            patch(
                "src.bot.handlers.free_text._core._humanize_assistant_response",
                new_callable=AsyncMock,
                return_value="Привет! Чем займёмся?",
            ),
            patch("src.bot.handlers.free_text._core._cache_last_humanized"),
            patch(
                "src.bot.handlers.free_text._core._detect_context_hint",
                return_value=None,
            ),
            patch(
                "src.bot.handlers.free_text._core.safe_answer", new_callable=AsyncMock
            ) as mock_safe,
            patch(
                "src.bot.handlers.free_text._core.sanitize_html",
                side_effect=lambda x: x,
            ),
            patch("src.bot.handlers.free_text._core._fire_record_trajectory"),
            patch(
                "src.bot.handlers.free_text._core._post_turn_optimize",
                new_callable=AsyncMock,
            ),
            patch(
                "src.bot.handlers.free_text._core.ctx_store.add_turn",
                new_callable=AsyncMock,
            ),
            patch("src.bot.handlers.free_text._core.track_ff"),
            patch("src.core.infra.task_manager.track_ff"),
            patch(
                "src.bot.handlers.free_text._core.asyncio.ensure_future",
                side_effect=self._make_fake_task,
            ),
        ):
            from src.bot.handlers.free_text._core import execute_instant

            result = await execute_instant(
                _mock_plan,
                _mock_msg,
                raw="Привет",
                owner_telegram_id=123456789,
                turn_started=_asyncio_mod.get_event_loop().time(),
                _via_dispatcher=True,
            )

        assert result is True
        assert mock_safe.call_count == 1, (
            f"safe_answer called {mock_safe.call_count} times (expected 1)"
        )

    @pytest.mark.asyncio
    async def test_cache_hit_no_dispatcher_sends_once_and_records_trajectory(
        self, _mock_plan, _mock_msg
    ):
        """Bug 1: _via_dispatcher=False + cache hit → 1 send + trajectory recorded."""
        from unittest.mock import AsyncMock, patch
        import time

        _mock_plan.metrics["route_cache_hit"] = True

        with (
            patch(
                "src.bot.handlers.free_text._core.check_pre_gate",
                return_value=None,
            ),
            patch(
                "src.bot.handlers.free_text._core._get_anti_ai_mode",
                new_callable=AsyncMock,
                return_value="normal",
            ),
            patch(
                "src.bot.handlers.free_text._core._humanize_assistant_response",
                new_callable=AsyncMock,
                return_value="Привет! Чем займёмся?",
            ),
            patch("src.bot.handlers.free_text._core._cache_last_humanized"),
            patch(
                "src.bot.handlers.free_text._core._detect_context_hint",
                return_value=None,
            ),
            patch(
                "src.bot.handlers.free_text._core.safe_answer", new_callable=AsyncMock
            ) as mock_safe,
            patch(
                "src.bot.handlers.free_text._core.sanitize_html",
                side_effect=lambda x: x,
            ),
            patch(
                "src.bot.handlers.free_text._core._fire_record_trajectory"
            ) as mock_traj,
            patch(
                "src.bot.handlers.free_text._core._post_turn_optimize",
                new_callable=AsyncMock,
            ) as mock_opt,
            patch(
                "src.bot.handlers.free_text._core.ctx_store.add_turn",
                new_callable=AsyncMock,
            ) as mock_ctx,
            patch("src.bot.handlers.free_text._core.track_ff"),
            patch("src.core.infra.task_manager.track_ff"),
            patch(
                "src.bot.handlers.free_text._core.asyncio.ensure_future",
                side_effect=self._make_fake_task,
            ),
        ):
            from src.bot.handlers.free_text._core import execute_instant

            result = await execute_instant(
                _mock_plan,
                _mock_msg,
                raw="Привет",
                owner_telegram_id=123456789,
                turn_started=time.monotonic(),
                _via_dispatcher=False,
            )

        assert result is True
        assert mock_safe.call_count == 1, (
            f"safe_answer called {mock_safe.call_count} times (expected 1)"
        )
        # Verify trajectory recorded with the actual humanized response
        mock_traj.assert_called_once()
        _, traj_kwargs = mock_traj.call_args
        assert traj_kwargs["response_text"] == "Привет! Чем займёмся?", (
            f"trajectory response_text={traj_kwargs['response_text']!r},"
            " expected 'Привет! Чем займёмся?'"
        )
        # Verify optimizer called with actual response
        mock_opt.assert_called_once_with(123456789, "Привет", "Привет! Чем займёмся?")
        # Verify context store
        mock_ctx.assert_called_once_with(123456789, "Привет", "Привет! Чем займёмся?")

    # ── Bug 2 tests ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_non_cache_path_uses_actual_response_not_plan_final_response(
        self, _mock_plan, _mock_msg
    ):
        """Bug 2: non-cache path records trajectory with actual response, not plan.final_response."""  # noqa: E501
        from unittest.mock import AsyncMock, MagicMock, patch
        import time

        _mock_plan.metrics["route_cache_hit"] = False
        _mock_plan.final_response = "ОРИГИНАЛЬНЫЙ ОТВЕТ LLM"  # noqa: RUF001

        # Mock DB to simulate a user with memory
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_owner = MagicMock()
        mock_owner.session = MagicMock()  # has_session = True
        mock_owner.alias = "Тест"

        mock_recall = MagicMock()
        mock_recall.facts = ["факт1"]  # noqa: RUF001

        with (
            patch(
                "src.bot.handlers.free_text._core.check_pre_gate",
                return_value=None,
            ),
            patch(
                "src.bot.handlers.free_text._core._get_anti_ai_mode",
                new_callable=AsyncMock,
                return_value="normal",
            ),
            patch(
                "src.bot.handlers.free_text._core._humanize_assistant_response",
                new_callable=AsyncMock,
                return_value="Добрый день, Тест! Чем займёмся?",
            ),
            patch("src.bot.handlers.free_text._core._cache_last_humanized"),
            patch(
                "src.bot.handlers.free_text._core._detect_context_hint",
                return_value=None,
            ),
            patch(
                "src.bot.handlers.free_text._core.safe_answer", new_callable=AsyncMock
            ),
            patch(
                "src.bot.handlers.free_text._core.sanitize_html",
                side_effect=lambda x: x,
            ),
            patch(
                "src.bot.handlers.free_text._core._fire_record_trajectory"
            ) as mock_traj,
            patch(
                "src.bot.handlers.free_text._core._post_turn_optimize",
                new_callable=AsyncMock,
            ) as mock_opt,
            patch(
                "src.bot.handlers.free_text._core.ctx_store.add_turn",
                new_callable=AsyncMock,
            ),
            patch("src.bot.handlers.free_text._core.track_ff"),
            patch("src.core.infra.task_manager.track_ff"),
            patch(
                "src.bot.handlers.free_text._core.asyncio.ensure_future",
                side_effect=self._make_fake_task,
            ),
            patch(
                "src.bot.handlers.free_text._core.get_session",
                return_value=mock_session,
            ),
            patch(
                "src.bot.handlers.free_text._core.get_or_create_user",
                new_callable=AsyncMock,
                return_value=mock_owner,
            ),
            patch(
                "src.bot.handlers.free_text._core.recall",
                new_callable=AsyncMock,
                return_value=mock_recall,
            ),
            patch(
                "src.bot.handlers.free_text._core._time_of_day_greeting",
                return_value="Добрый день",
            ),
        ):
            from src.bot.handlers.free_text._core import execute_instant

            result = await execute_instant(
                _mock_plan,
                _mock_msg,
                raw="Привет",
                owner_telegram_id=123456789,
                turn_started=time.monotonic(),
                _via_dispatcher=False,
            )

        assert result is True
        # Verify trajectory was called with actual response, NOT plan.final_response
        mock_traj.assert_called_once()
        _, traj_kwargs = mock_traj.call_args
        assert traj_kwargs["response_text"] == "Добрый день, Тест! Чем займёмся?", (
            f"BUG 2: trajectory response_text={traj_kwargs['response_text']!r}, "
            f"expected 'Добрый день, Тест! Чем займёмся?'"
        )
        assert traj_kwargs["response_text"] != _mock_plan.final_response, (
            f"BUG 2: trajectory uses plan.final_response="
            f"{traj_kwargs['response_text']!r}"
        )
        # Verify optimizer called with actual response
        mock_opt.assert_called_once_with(
            123456789, "Привет", "Добрый день, Тест! Чем займёмся?"
        )
