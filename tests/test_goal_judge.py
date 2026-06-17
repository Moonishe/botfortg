"""Tests for Goal Judge component.

Uses a mock LLM provider to test GoalVerdict validation,
GoalJudgeLLM parsing, and error/fallback paths.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.pipeline.goal_judge import (
    GoalJudgeLLM,
    GoalVerdict,
    create_goal_judge,
)
from src.llm.base import ChatMessage, TaskType


# ═══════════════════════════════════════════════════════════════════════
# GoalVerdict tests
# ═══════════════════════════════════════════════════════════════════════


class TestGoalVerdict:
    """Тесты модели GoalVerdict."""

    def test_valid_ok(self) -> None:
        v = GoalVerdict(
            ok=True, impossible=False, reason="Goal achieved", confidence=0.9
        )
        assert v.ok is True
        assert v.impossible is False
        assert v.is_goal_achieved() is True

    def test_valid_impossible(self) -> None:
        v = GoalVerdict(
            ok=False, impossible=True, reason="Blocked by permissions", confidence=1.0
        )
        assert v.ok is False
        assert v.impossible is True
        assert v.is_goal_achieved() is False

    def test_valid_neither(self) -> None:
        v = GoalVerdict(
            ok=False, impossible=False, reason="Judge unavailable", confidence=0.0
        )
        assert v.ok is False
        assert v.impossible is False
        assert v.is_goal_achieved() is False

    def test_both_true_raises(self) -> None:
        with pytest.raises(ValueError, match="ok and impossible cannot both be True"):
            GoalVerdict(ok=True, impossible=True, reason="Invalid", confidence=0.5)

    def test_confidence_clamped(self) -> None:
        """Boundary values for confidence."""
        v_min = GoalVerdict(ok=False, impossible=False, reason="min", confidence=0.0)
        assert v_min.confidence == 0.0
        v_max = GoalVerdict(ok=False, impossible=False, reason="max", confidence=1.0)
        assert v_max.confidence == 1.0

    def test_confidence_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            GoalVerdict(ok=False, impossible=False, reason="bad", confidence=1.5)
        with pytest.raises(ValueError):
            GoalVerdict(ok=False, impossible=False, reason="bad", confidence=-0.1)

    def test_reason_too_long(self) -> None:
        with pytest.raises(ValueError):
            GoalVerdict(ok=False, impossible=False, reason="x" * 2001, confidence=0.5)

    def test_reason_empty(self) -> None:
        with pytest.raises(ValueError):
            GoalVerdict(ok=False, impossible=False, reason="", confidence=0.5)


# ═══════════════════════════════════════════════════════════════════════
# Mock-хелперы
# ═══════════════════════════════════════════════════════════════════════


def _make_mock_session() -> AsyncMock:
    """Создать mock AsyncSession."""
    return MagicMock(spec=[])


def _make_mock_user() -> MagicMock:
    """Создать mock User."""
    user = MagicMock()
    user.telegram_id = 123456
    user.settings = MagicMock()
    user.settings.llm_provider = "openai"
    user.settings.use_heavy_model = False
    return user


def _make_ok_response() -> str:
    return json.dumps(
        {
            "ok": True,
            "impossible": False,
            "reason": "All tasks completed successfully",
            "confidence": 0.95,
        }
    )


def _make_impossible_response() -> str:
    return json.dumps(
        {
            "ok": False,
            "impossible": True,
            "reason": "Missing required permissions",
            "confidence": 1.0,
        }
    )


# ═══════════════════════════════════════════════════════════════════════
# GoalJudgeLLM tests
# ═══════════════════════════════════════════════════════════════════════


class TestGoalJudgeLLM:
    """Тесты GoalJudgeLLM."""

    @pytest.fixture
    def session(self) -> AsyncMock:
        return _make_mock_session()

    @pytest.fixture
    def user(self) -> MagicMock:
        return _make_mock_user()

    @pytest.fixture
    def transcript(self) -> list[ChatMessage]:
        return [
            ChatMessage(role="user", content="Write a function"),
            ChatMessage(
                role="assistant", content="Here is the function: def foo(): pass"
            ),
        ]

    def test_create_goal_judge(self, session: AsyncMock, user: MagicMock) -> None:
        judge = create_goal_judge(session, user)
        assert isinstance(judge, GoalJudgeLLM)
        assert judge._session is session
        assert judge._user is user

    @pytest.mark.asyncio
    async def test_judge_ok(
        self, session: AsyncMock, user: MagicMock, transcript: list[ChatMessage]
    ) -> None:
        """LLM возвращает ok=True."""
        mock_provider = AsyncMock()
        mock_provider.chat.return_value = _make_ok_response()

        with patch(
            "src.llm.provider_manager.build_provider",
            new=AsyncMock(return_value=mock_provider),
        ):
            judge = GoalJudgeLLM(session, user)
            verdict = await judge.judge("Complete task", transcript)

        assert verdict.ok is True
        assert verdict.impossible is False
        assert verdict.confidence == 0.95
        assert verdict.is_goal_achieved() is True
        assert "successfully" in verdict.reason

    @pytest.mark.asyncio
    async def test_judge_impossible(
        self, session: AsyncMock, user: MagicMock, transcript: list[ChatMessage]
    ) -> None:
        """LLM возвращает impossible=True."""
        mock_provider = AsyncMock()
        mock_provider.chat.return_value = _make_impossible_response()

        with patch(
            "src.llm.provider_manager.build_provider",
            new=AsyncMock(return_value=mock_provider),
        ):
            judge = GoalJudgeLLM(session, user)
            verdict = await judge.judge("Do the impossible", transcript)

        assert verdict.ok is False
        assert verdict.impossible is True
        assert verdict.is_goal_achieved() is False

    @pytest.mark.asyncio
    async def test_judge_provider_none(
        self, session: AsyncMock, user: MagicMock, transcript: list[ChatMessage]
    ) -> None:
        """build_provider возвращает None → fallback."""
        with patch(
            "src.llm.provider_manager.build_provider",
            new=AsyncMock(return_value=None),
        ):
            judge = GoalJudgeLLM(session, user)
            verdict = await judge.judge("test goal", transcript)

        assert verdict.ok is False
        assert verdict.impossible is False
        assert verdict.reason == "Judge unavailable"
        assert verdict.confidence == 0.0

    @pytest.mark.asyncio
    async def test_judge_llm_exception(
        self, session: AsyncMock, user: MagicMock, transcript: list[ChatMessage]
    ) -> None:
        """LLM выбрасывает исключение → fallback."""
        mock_provider = AsyncMock()
        mock_provider.chat.side_effect = RuntimeError("LLM exploded")

        with patch(
            "src.llm.provider_manager.build_provider",
            new=AsyncMock(return_value=mock_provider),
        ):
            judge = GoalJudgeLLM(session, user)
            verdict = await judge.judge("test goal", transcript)

        assert verdict.ok is False
        assert verdict.impossible is False
        assert verdict.reason == "Judge unavailable"
        assert verdict.confidence == 0.0

    @pytest.mark.asyncio
    async def test_judge_unparseable_json(
        self, session: AsyncMock, user: MagicMock, transcript: list[ChatMessage]
    ) -> None:
        """LLM возвращает невалидный JSON."""
        mock_provider = AsyncMock()
        mock_provider.chat.return_value = "not json at all"

        with patch(
            "src.llm.provider_manager.build_provider",
            new=AsyncMock(return_value=mock_provider),
        ):
            judge = GoalJudgeLLM(session, user)
            verdict = await judge.judge("test goal", transcript)

        assert verdict.ok is False
        assert verdict.impossible is False
        assert "unparseable" in verdict.reason.lower()

    @pytest.mark.asyncio
    async def test_judge_both_true_json(
        self, session: AsyncMock, user: MagicMock, transcript: list[ChatMessage]
    ) -> None:
        """LLM возвращает ok=True, impossible=True → fallback (валидация отклоняет)."""
        mock_provider = AsyncMock()
        mock_provider.chat.return_value = json.dumps(
            {
                "ok": True,
                "impossible": True,
                "reason": "both true — invalid",
                "confidence": 0.9,
            }
        )

        with patch(
            "src.llm.provider_manager.build_provider",
            new=AsyncMock(return_value=mock_provider),
        ):
            judge = GoalJudgeLLM(session, user)
            verdict = await judge.judge("test goal", transcript)

        assert verdict.ok is False
        assert verdict.impossible is False

    @pytest.mark.asyncio
    async def test_parse_response_markdown_fence(self) -> None:
        """Парсинг ответа, обёрнутого в ```json ... ```."""
        raw = "```json\n" + _make_ok_response() + "\n```"
        verdict = GoalJudgeLLM._parse_response(raw)
        assert verdict is not None
        assert verdict.ok is True
        assert verdict.confidence == 0.95

    @pytest.mark.asyncio
    async def test_sanitize_in_transcript(
        self, session: AsyncMock, user: MagicMock
    ) -> None:
        """HTML-теги в транскрипте санитизируются."""
        mock_provider = AsyncMock()
        mock_provider.chat.return_value = _make_ok_response()

        # Транскрипт с потенциально опасным HTML
        xss_transcript = [
            ChatMessage(role="user", content="<script>alert(1)</script>"),
        ]

        captured_messages: list[list[ChatMessage]] = []

        async def capture_chat(messages, **kwargs):
            captured_messages.append(messages)
            return _make_ok_response()

        mock_provider.chat = capture_chat

        with patch(
            "src.llm.provider_manager.build_provider",
            new=AsyncMock(return_value=mock_provider),
        ):
            judge = GoalJudgeLLM(session, user)
            verdict = await judge.judge("test", xss_transcript)

        assert verdict.ok is True
        # Проверим, что в отправленных сообщениях нет сырого <script>
        for msg_batch in captured_messages:
            for msg in msg_batch:
                assert "<script>" not in msg.content

    @pytest.mark.asyncio
    async def test_build_provider_called_with_goal_judge_task_type(
        self, session: AsyncMock, user: MagicMock, transcript: list[ChatMessage]
    ) -> None:
        """Проверить, что build_provider вызывается с TaskType.GOAL_JUDGE."""
        mock_provider = AsyncMock()
        mock_provider.chat.return_value = _make_ok_response()

        mock_build = AsyncMock(return_value=mock_provider)

        with patch(
            "src.llm.provider_manager.build_provider",
            new=mock_build,
        ):
            judge = GoalJudgeLLM(session, user)
            await judge.judge("test", transcript)

        # Проверяем, что build_provider вызван с task_type=TaskType.GOAL_JUDGE
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs["task_type"] == TaskType.GOAL_JUDGE
        assert call_kwargs["purpose"] == "goal_judge"


# ═══════════════════════════════════════════════════════════════════════
# Интеграционные тесты (create_goal_judge)
# ═══════════════════════════════════════════════════════════════════════


class TestCreateGoalJudge:
    """Тесты фабрики create_goal_judge."""

    def test_returns_goal_judge_llm(self) -> None:
        session = _make_mock_session()
        user = _make_mock_user()
        judge = create_goal_judge(session, user)
        assert isinstance(judge, GoalJudgeLLM)
