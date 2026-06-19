"""Tests for Unified Dispatcher (src/bot/dispatcher.py).

Verifies:
- DispatchResult dataclass fields
- UnifiedDispatcher.dispatch() with instant/fast_route/maestro plans
- Pre-gate short-circuit
- Post-processing hooks
- Feature flag backward compatibility
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")


class TestDispatchResult:
    """Test DispatchResult dataclass."""

    def test_default_fields(self):
        from src.core.dispatcher import DispatchResult

        r = DispatchResult()
        assert r.handled is True
        assert r.response_text == ""
        assert r.route_mode == ""
        assert r.success is True
        assert r.error is None
        assert r.extra == {}
        assert r.skip_humanize is False
        assert r.skip_trajectory is False
        assert r.skip_session_log is False
        assert r.skip_post_turn is False

    def test_custom_fields(self):
        from src.core.dispatcher import DispatchResult

        r = DispatchResult(
            handled=False,
            response_text="test",
            route_mode="maestro",
            success=False,
            error="some error",
            skip_humanize=True,
        )
        assert r.handled is False
        assert r.response_text == "test"
        assert r.route_mode == "maestro"
        assert r.success is False
        assert r.error == "some error"
        assert r.skip_humanize is True


class TestUnifiedDispatcher:
    """Test UnifiedDispatcher."""

    @pytest.fixture
    def mock_message(self):
        msg = MagicMock()
        msg.from_user.id = 123456789
        msg.answer = AsyncMock()
        msg.edit_text = AsyncMock()
        return msg

    @pytest.fixture
    def mock_plan(self):
        plan = MagicMock()
        plan.response_mode = "instant"
        plan.final_response = "Hello!"
        plan.metrics = {}
        plan.tasks = []
        return plan

    @pytest.mark.asyncio
    async def test_dispatch_instant_calls_execute_instant(
        self, mock_message, mock_plan
    ):
        """When response_mode='instant', dispatcher calls execute_instant with _via_dispatcher=True."""
        from src.bot.dispatcher import UnifiedDispatcher

        dispatcher = UnifiedDispatcher()

        with (
            patch("src.bot.dispatcher.UnifiedDispatcher._post_process") as mock_pp,
            patch(
                "src.bot.dispatcher.execute_instant",
                new_callable=AsyncMock,
            ) as mock_exec_instant,
            patch(
                "src.bot.dispatcher.safe_answer",
                new_callable=AsyncMock,
            ),
            patch(
                "src.bot.dispatcher.check_pre_gate",
                return_value=None,
            ),
            patch("src.bot.dispatcher.track_ff"),
        ):
            mock_exec_instant.return_value = True

            result = await dispatcher.dispatch(
                raw="hello",
                plan=mock_plan,
                provider=None,
                message=mock_message,
                state=None,
                userbot_manager=None,
                owner_telegram_id=123456789,
                tz_name="UTC",
                history_block="",
                turn_started=0.0,
            )

            # execute_instant should be called with _via_dispatcher=True
            mock_exec_instant.assert_called_once()
            call_kwargs = mock_exec_instant.call_args.kwargs
            assert call_kwargs.get("_via_dispatcher") is True

            # Post-processing should be called
            mock_pp.assert_called_once()
            assert result.route_mode == "instant"

    @pytest.mark.asyncio
    async def test_pre_gate_short_circuit(self, mock_message, mock_plan):
        """When check_pre_gate returns non-None, return DispatchResult with route_mode='pre_gate'."""
        from src.bot.dispatcher import UnifiedDispatcher

        dispatcher = UnifiedDispatcher()

        with (
            patch(
                "src.bot.dispatcher.check_pre_gate",
                return_value="Привет!",
            ),
            patch("src.bot.dispatcher.safe_answer", new_callable=AsyncMock),
            patch(
                "src.bot.dispatcher.execute_instant",
                new_callable=AsyncMock,
            ),
            patch("src.bot.dispatcher.UnifiedDispatcher._post_process"),
            patch("src.bot.dispatcher.track_ff"),
        ):
            result = await dispatcher.dispatch(
                raw="привет",
                plan=mock_plan,
                provider=None,
                message=mock_message,
                state=None,
                userbot_manager=None,
                owner_telegram_id=123456789,
                tz_name="UTC",
                history_block="",
                turn_started=0.0,
            )

            assert result.route_mode == "pre_gate"
            assert result.skip_humanize is True
            # safe_answer should have been called
            from src.bot.dispatcher import safe_answer

            safe_answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_post_process_called(self, mock_message, mock_plan):
        """Post-processing hooks (_fire_record_trajectory etc.) are called."""
        from src.bot.dispatcher import UnifiedDispatcher

        dispatcher = UnifiedDispatcher()

        with (
            patch("src.bot.dispatcher.check_pre_gate", return_value=None),
            patch("src.bot.dispatcher.safe_answer", new_callable=AsyncMock),
            patch(
                "src.bot.dispatcher.execute_instant",
                new_callable=AsyncMock,
            ) as mock_exec,
            patch(
                "src.bot.dispatcher._fire_record_trajectory",
            ) as mock_traj,
            patch(
                "src.bot.dispatcher.track_ff"
            ),  # no-op: prevent fire-and-forget DB tasks
            patch("src.bot.dispatcher._post_turn_optimize"),
        ):
            mock_exec.return_value = True

            await dispatcher.dispatch(
                raw="hello",
                plan=mock_plan,
                provider=None,
                message=mock_message,
                state=None,
                userbot_manager=None,
                owner_telegram_id=123456789,
                tz_name="UTC",
                history_block="",
                turn_started=0.0,
            )

            # _fire_record_trajectory should be called in _post_process
            mock_traj.assert_called_once()
            call_kwargs = mock_traj.call_args.kwargs
            assert call_kwargs.get("route_mode") == "instant"


class TestFeatureFlag:
    """Test feature flag backward compatibility."""

    def test_use_unified_dispatcher_defaults_false(self):
        """When use_unified_dispatcher is not set, it defaults to False."""
        from src.config import Settings

        # With minimal env, default should be False
        # Settings class defaults are read from Field(default=...)
        assert Settings.model_fields["use_unified_dispatcher"].default is False

    def test_use_unified_dispatcher_field_exists(self):
        """The field exists in Settings and is a bool."""
        from src.config import Settings

        field_info = Settings.model_fields["use_unified_dispatcher"]
        # Check annotation
        assert field_info.annotation is bool or "bool" in str(field_info.annotation)
        assert field_info.default is False
