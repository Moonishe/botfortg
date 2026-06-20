"""Tests for Week 6 Skills Lifecycle callbacks.

Covers:
  - Single-skill evolve callback
  - Global evolve dry-run callback
  - Global evolve apply callback
  - UI helpers for evolve dry-run report
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"  # noqa: S105

from src.bot.handlers.skills_callbacks import (
    cb_skill_evolve,
    cb_skills_evolve_apply,
    cb_skills_evolve_dryrun,
)
from src.bot.handlers.skills_ui import _format_evolve_dryrun

OWNER_TG_ID = 123456789


def _make_skill(
    skill_id: int = 1,
    name: str = "test-skill",
    enabled: bool = True,
    review_status: str = "approved",
    success_count: int = 5,
    failure_count: int = 10,
    validation_score: float | None = 0.4,
    last_used_at=None,
    best_body: str | None = None,
) -> MagicMock:
    skill = MagicMock()
    skill.id = skill_id
    skill.name = name
    skill.enabled = enabled
    skill.review_status = review_status
    skill.success_count = success_count
    skill.failure_count = failure_count
    skill.validation_score = validation_score
    skill.last_used_at = last_used_at
    skill.best_body = best_body
    skill.body = "body"
    skill.version = "1.0.0"
    skill.description = ""
    skill.edit_history_json = []
    skill.rejected_edits_json = []
    return skill


def _make_callback(data: str = "skills:evolve_one:1") -> MagicMock:
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = OWNER_TG_ID
    cb.data = data
    cb.message = MagicMock()
    cb.message.from_user = MagicMock()
    cb.message.from_user.id = OWNER_TG_ID
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


# ── UI helpers ───────────────────────────────────────────────────────


def test_format_evolve_dryrun_with_candidates():
    """Dry-run report shows candidate skills and apply button."""
    candidates = [_make_skill(skill_id=1), _make_skill(skill_id=2, name="other")]
    text, kb = _format_evolve_dryrun(candidates)
    assert "test-skill" in text
    assert "other" in text
    assert "Кандидатов: 2" in text
    assert any(
        "evolve_apply" in (btn.callback_data or "")
        for row in kb.inline_keyboard
        for btn in row
    )


def test_format_evolve_dryrun_empty():
    """Empty dry-run shows no candidates and no apply button."""
    text, kb = _format_evolve_dryrun([])
    assert "Нет underperforming skills" in text
    assert not any(
        "evolve_apply" in (btn.callback_data or "")
        for row in kb.inline_keyboard
        for btn in row
    )


# ── Callbacks ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_skill_evolve_success():
    """Single-skill evolve callback shows result."""
    cb = _make_callback("skills:evolve_one:1")
    skill = _make_skill()
    owner = MagicMock()
    owner.id = 1
    owner.telegram_id = OWNER_TG_ID

    with (
        patch("src.bot.handlers.skills_callbacks.get_session") as mock_session_cls,
        patch(
            "src.bot.handlers.skills_callbacks.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.skills_callbacks._get_skill_by_id",
            AsyncMock(return_value=skill),
        ),
        patch(
            "src.bot.handlers.skills_callbacks.evolve_skill",
            AsyncMock(
                return_value={
                    "success": True,
                    "applied": True,
                    "reason": "Evolved v1.1.0",
                }
            ),
        ),
    ):
        session_mock = MagicMock()
        session_mock.refresh = AsyncMock()
        session_mock.expunge = MagicMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session_mock)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        await cb_skill_evolve(cb)

    cb.answer.assert_called_once()
    assert cb.message.edit_text.called


@pytest.mark.asyncio
async def test_cb_skills_evolve_dryrun():
    """Dry-run callback lists underperforming skills."""
    cb = _make_callback("skills:evolve_dryrun:0")
    skill = _make_skill()

    with patch(
        "src.bot.handlers.skills_callbacks.find_underperforming_skills",
        AsyncMock(return_value=[skill]),
    ):
        await cb_skills_evolve_dryrun(cb)

    cb.answer.assert_called_once()
    text = cb.message.edit_text.call_args[0][0]
    assert "Кандидатов: 1" in text
    assert "test-skill" in text


@pytest.mark.asyncio
async def test_cb_skills_evolve_apply():
    """Apply callback runs evolution and reports counts."""
    cb = _make_callback("skills:evolve_apply:0")
    skill = _make_skill()

    with (
        patch(
            "src.bot.handlers.skills_callbacks.find_underperforming_skills",
            AsyncMock(return_value=[skill]),
        ),
        patch(
            "src.bot.handlers.skills_callbacks.evolve_skill",
            AsyncMock(
                return_value={
                    "success": True,
                    "applied": True,
                    "reason": "Evolved v1.1.0",
                    "skill_name": "test-skill",
                }
            ),
        ),
    ):
        await cb_skills_evolve_apply(cb)

    cb.answer.assert_called_once()
    text, _ = cb.message.edit_text.call_args
    assert "Применено: 1" in text[0]
    assert "test-skill" in text[0]


@pytest.mark.asyncio
async def test_cb_skills_evolve_apply_empty():
    """Apply callback with no candidates returns friendly message."""
    cb = _make_callback("skills:evolve_apply:0")

    with patch(
        "src.bot.handlers.skills_callbacks.find_underperforming_skills",
        AsyncMock(return_value=[]),
    ):
        await cb_skills_evolve_apply(cb)

    cb.answer.assert_called_once()
    text, _ = cb.message.edit_text.call_args
    assert "Нет кандидатов" in text[0]


@pytest.mark.asyncio
async def test_cb_skills_evolve_dryrun_error():
    """Dry-run callback handles failure gracefully."""
    cb = _make_callback("skills:evolve_dryrun:0")

    with patch(
        "src.bot.handlers.skills_callbacks.find_underperforming_skills",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await cb_skills_evolve_dryrun(cb)

    cb.answer.assert_called_once_with(
        "⚠️ Не удалось найти кандидатов",  # noqa: RUF001
        show_alert=True,
    )


# ── Detail keyboard includes evolve button ───────────────────────────


def test_skill_detail_keyboard_has_evolve():
    """Active skill detail view includes Evolve button."""
    from src.bot.handlers.skills_ui import _skill_detail_keyboard

    skill = _make_skill()
    kb = _skill_detail_keyboard(skill)
    flat = [btn.callback_data or "" for row in kb.inline_keyboard for btn in row]
    assert any("evolve_one" in data for data in flat)


def test_skill_list_keyboard_has_dryrun():
    """Skill list keyboard has dry-run button."""
    from src.bot.handlers.skills_ui import _skill_list_keyboard

    skill = _make_skill()
    kb = _skill_list_keyboard([skill], "all", 0, 1)
    flat = [btn.callback_data or "" for row in kb.inline_keyboard for btn in row]
    assert any("evolve_dryrun" in data for data in flat)
    assert not any("evolve:0" in data for data in flat)


# ── Edge case tests (Debugger 4) ──────────────────────────────────────


class TestFormatMetricsEdgeCases:
    """Edge cases for _format_metrics."""

    def test_negative_counts_clamped(self):
        """Negative success/failure counts are clamped to 0."""
        from src.bot.handlers.skills_ui import _format_metrics

        skill = _make_skill(success_count=-5, failure_count=-3)
        text = _format_metrics(skill)
        assert "исп:0" in text
        assert "усп:0" in text
        assert "неусп:0" in text
        assert "sr:0%" in text

    def test_validation_score_above_one_clamped(self):
        """validation_score > 1.0 is clamped to 100%."""
        from src.bot.handlers.skills_ui import _format_metrics

        skill = _make_skill(validation_score=1.5, success_count=1)
        text = _format_metrics(skill)
        assert "score:100%" in text
        assert "score:150%" not in text

    def test_validation_score_negative_clamped(self):
        """Negative validation_score is clamped to 0%."""
        from src.bot.handlers.skills_ui import _format_metrics

        skill = _make_skill(validation_score=-0.3, success_count=1)
        text = _format_metrics(skill)
        assert "score:0%" in text

    def test_all_zero_metrics(self):
        """All metrics at zero/null produces sensible output."""
        from src.bot.handlers.skills_ui import _format_metrics

        skill = _make_skill(
            success_count=0,
            failure_count=0,
            validation_score=None,
            last_used_at=None,
        )
        text = _format_metrics(skill)
        assert "исп:0" in text
        assert "sr:0%" in text
        assert "score:—" in text
        assert "last:—" in text


class TestFormatEvolveDryrunEdgeCases:
    """Edge cases for _format_evolve_dryrun."""

    def test_candidate_with_none_name(self):
        """Candidate with name=None shows 'Unnamed'."""
        from src.bot.handlers.skills_ui import _format_evolve_dryrun

        skill = _make_skill()
        skill.name = None  # Simulate corrupted name
        text, _kb = _format_evolve_dryrun([skill])
        assert "Unnamed" in text
        assert "Кандидатов: 1" in text

    def test_candidate_with_empty_name(self):
        """Candidate with name='' shows 'Unnamed'."""
        from src.bot.handlers.skills_ui import _format_evolve_dryrun

        skill = _make_skill(name="")
        text, _kb = _format_evolve_dryrun([skill])
        assert "Unnamed" in text

    def test_candidate_with_zero_metrics(self):
        """Candidate with zero metrics rendered safely."""
        from src.bot.handlers.skills_ui import _format_evolve_dryrun

        skill = _make_skill(
            success_count=0,
            failure_count=0,
            validation_score=None,
            last_used_at=None,
        )
        text, _kb = _format_evolve_dryrun([skill])
        assert "test-skill" in text
        assert "Кандидатов: 1" in text


class TestCbEvolveApplyEdgeCases:
    """Edge cases for cb_skills_evolve_apply."""

    @pytest.mark.asyncio
    async def test_missing_skill_name_in_result(self):
        """Result dict without 'skill_name' key uses 'Unnamed' fallback."""
        cb = _make_callback("skills:evolve_apply:0")
        skill = _make_skill()

        with (
            patch(
                "src.bot.handlers.skills_callbacks.find_underperforming_skills",
                AsyncMock(return_value=[skill]),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.evolve_skill",
                AsyncMock(
                    return_value={
                        "success": True,
                        "applied": True,
                        "reason": "ok",
                        # No skill_name key!
                    }
                ),
            ),
        ):
            await cb_skills_evolve_apply(cb)

        text, _ = cb.message.edit_text.call_args
        assert "Применено: 1" in text[0]
        assert "Unnamed" in text[0]

    @pytest.mark.asyncio
    async def test_all_evolutions_fail_empty_results(self):
        """All evolutions raise exceptions → results report failures correctly."""
        cb = _make_callback("skills:evolve_apply:0")
        skill = _make_skill()

        with (
            patch(
                "src.bot.handlers.skills_callbacks.find_underperforming_skills",
                AsyncMock(return_value=[skill]),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.evolve_skill",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            await cb_skills_evolve_apply(cb)

        text, _ = cb.message.edit_text.call_args
        assert "Ошибок: 1" in text[0]
        assert "Error: boom" in text[0]

    @pytest.mark.asyncio
    async def test_evolve_skill_raises_inner_exception(self):
        """evolve_skill raises → caught by outer handler, reports error."""
        cb = _make_callback("skills:evolve_one:1")
        skill = _make_skill()
        owner = MagicMock()
        owner.id = 1
        owner.telegram_id = OWNER_TG_ID

        with (
            patch("src.bot.handlers.skills_callbacks.get_session") as mock_sess,
            patch(
                "src.bot.handlers.skills_callbacks.get_or_create_user",
                AsyncMock(return_value=owner),
            ),
            patch(
                "src.bot.handlers.skills_callbacks._get_skill_by_id",
                AsyncMock(return_value=skill),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.evolve_skill",
                AsyncMock(side_effect=RuntimeError("unexpected crash")),
            ),
        ):
            session_mock = MagicMock()
            session_mock.refresh = AsyncMock()
            session_mock.expunge = MagicMock()
            mock_sess.return_value.__aenter__ = AsyncMock(return_value=session_mock)
            mock_sess.return_value.__aexit__ = AsyncMock(return_value=False)
            await cb_skill_evolve(cb)

        cb.answer.assert_called_with(
            "Не удалось эволюционировать skill",  # noqa: RUF001
            show_alert=True,
        )

    @pytest.mark.asyncio
    async def test_malformed_callback_in_evolve_dryrun(self):
        """Corrupted callback data in evolve_dryrun still works — empty result."""
        cb = _make_callback("skills:evolve_dryrun:0")
        cb.data = None  # Simulate corrupted callback data
        with patch(
            "src.bot.handlers.skills_callbacks.find_underperforming_skills",
            AsyncMock(return_value=[]),
        ):
            await cb_skills_evolve_dryrun(cb)

        text, _ = cb.message.edit_text.call_args
        assert "Нет underperforming skills" in text[0]
