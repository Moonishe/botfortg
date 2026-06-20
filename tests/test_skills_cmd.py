"""Unit tests for skills inline UI — src/bot/handlers/skills_cmd.py.

Covers:
  - UI status mapping and metric formatting
  - Inline keyboard builders
  - /skills command default panel
  - Callback handlers (page, detail, approve, toggle, rollback, stats)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.filters import CommandObject
from sqlalchemy import select

os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"  # noqa: S105
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.bot.handlers.skills_cmd import (
    cmd_skills,
)
from src.bot.handlers.skills_callbacks import (
    cb_skill_approve,
    cb_skill_detail,
    cb_skill_promote,
    cb_skill_reject,
    cb_skill_rollback,
    cb_skill_toggle,
    cb_skills_evolve_apply,
    cb_skills_evolve_dryrun,
    cb_skills_page,
    cb_skills_stats,
)
from src.bot.handlers.skills_callbacks import _parse_callback_skill_id
from src.bot.handlers.skills_data import _status_filter_clauses
from src.bot.handlers.skills_ui import (
    _DECAY_MARKER,
    _SUCCESS_RATE_MARKER,
    _format_metrics,
    _format_skill_detail,
    _is_stale,
    _skill_button,
    _skill_detail_keyboard,
    _skill_list_keyboard,
    _ui_status,
)

OWNER_TG_ID = 123456789


# ── Helpers ─────────────────────────────────────────────────────────


def _make_skill(
    name: str = "test-skill",
    review_status: str = "approved",
    enabled: bool = True,
    success_count: int = 0,
    failure_count: int = 0,
    validation_score: float | None = None,
    last_used_at: datetime | None = None,
    best_body: str | None = None,
    description: str = "",
    body: str = "body",
    skill_id: int = 1,
    version: str = "1.0.0",
) -> MagicMock:
    """Create a lightweight mock skill mirroring the DB model fields."""
    skill = MagicMock()
    skill.id = skill_id
    skill.name = name
    skill.description = description
    skill.body = body
    skill.enabled = enabled
    skill.review_status = review_status
    skill.success_count = success_count
    skill.failure_count = failure_count
    skill.validation_score = validation_score
    skill.last_used_at = last_used_at
    skill.best_body = best_body
    skill.version = version
    skill.edit_history_json = None
    skill.rejected_edits_json = None
    return skill


def _make_message(user_id: int = OWNER_TG_ID, text: str = "/skills") -> MagicMock:
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _make_callback(
    user_id: int = OWNER_TG_ID, data: str = "skills:page:all:0"
) -> MagicMock:
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _mock_session_context():
    """Patch get_session, get_or_create_user for handlers.

    Returns the owner mock so the caller can assert on it.
    """
    owner = MagicMock()
    owner.id = 1
    owner.telegram_id = OWNER_TG_ID

    class _FakeSession:
        async def __aenter__(self):
            session = MagicMock()
            session.flush = AsyncMock()
            session.refresh = AsyncMock()
            result_mock = MagicMock()
            result_mock.scalar = MagicMock(return_value=0)
            session.execute = AsyncMock(return_value=result_mock)
            return session

        async def __aexit__(self, *args):
            return False

    patches = [
        patch("src.bot.handlers.skills_cmd.get_session", _FakeSession),
        patch(
            "src.bot.handlers.skills_cmd.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch("src.bot.handlers.skills_callbacks.get_session", _FakeSession),
        patch(
            "src.bot.handlers.skills_callbacks.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
    ]
    return owner, patches


# ── Tests: UI status helpers ─────────────────────────────────────────


class TestUIStatus:
    def test_rejected_status(self):
        skill = _make_skill(review_status="rejected")
        assert _ui_status(skill) == "rejected"

    def test_proposed_status(self):
        skill = _make_skill(review_status="proposed", enabled=False)
        assert _ui_status(skill) == "proposed"

    def test_active_status(self):
        skill = _make_skill(review_status="approved", enabled=True)
        assert _ui_status(skill) == "active"

    def test_stale_status(self):
        skill = _make_skill(
            review_status="approved",
            enabled=False,
            description="[DECAYED] low success_rate",
        )
        assert _ui_status(skill) == "stale"

    def test_archived_status(self):
        skill = _make_skill(review_status="approved", enabled=False)
        assert _ui_status(skill) == "archived"


class TestFormatMetrics:
    def test_zero_usage(self):
        skill = _make_skill()
        text = _format_metrics(skill)
        assert "исп:0" in text
        assert "sr:0%" in text

    def test_with_usage(self):
        skill = _make_skill(success_count=3, failure_count=1)
        text = _format_metrics(skill)
        assert "исп:4" in text
        assert "sr:75%" in text

    def test_validation_score(self):
        skill = _make_skill(validation_score=0.85)
        assert "score:85%" in _format_metrics(skill)

    def test_last_used(self):
        skill = _make_skill(last_used_at=datetime(2026, 6, 15, 12, 0, tzinfo=UTC))
        assert "last:15.06" in _format_metrics(skill)


# ── Tests: Keyboard builders ─────────────────────────────────────────


class TestSkillButton:
    def test_label_and_callback(self):
        skill = _make_skill(name="my-skill", skill_id=7)
        btn = _skill_button(skill)
        assert btn.text == "✅ my-skill"
        assert btn.callback_data == "skills:detail:7"

    def test_proposed_icon(self):
        skill = _make_skill(review_status="proposed", skill_id=3)
        btn = _skill_button(skill)
        assert "🆕" in btn.text


class TestSkillListKeyboard:
    def test_has_status_tabs(self):
        skill = _make_skill()
        kb = _skill_list_keyboard([skill], "all", 0, 1)
        assert kb.inline_keyboard
        # First 5 rows should be status tabs
        texts = [b.text for row in kb.inline_keyboard[:5] for b in row]
        assert any("Все ✅" in t for t in texts)  # noqa: RUF001
        assert any("Предложен" in t for t in texts)
        assert any("Активен" in t for t in texts)

    def test_pagination_when_more_skills(self):
        skills = [_make_skill(name=f"s{i}", skill_id=i) for i in range(6)]
        kb = _skill_list_keyboard(skills, "all", 0, 6)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert "Вперёд ▶️" in texts

    def test_no_pagination_when_fits(self):
        skill = _make_skill()
        kb = _skill_list_keyboard([skill], "all", 0, 1)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert "Вперёд ▶️" not in texts

    def test_evolve_and_stats_buttons(self):
        skill = _make_skill()
        kb = _skill_list_keyboard([skill], "all", 0, 1)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Evolve dry-run" in t for t in texts)
        assert any("Stats" in t for t in texts)


class TestSkillDetailKeyboard:
    def test_proposed_actions(self):
        skill = _make_skill(review_status="proposed", enabled=False)
        kb = _skill_detail_keyboard(skill)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Approve" in t for t in texts)
        assert any("Reject" in t for t in texts)

    def test_active_actions(self):
        skill = _make_skill(best_body="best")
        kb = _skill_detail_keyboard(skill)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Disable" in t for t in texts)
        assert any("Rollback" in t for t in texts)
        assert any("Promote" in t for t in texts)

    def test_disabled_action(self):
        skill = _make_skill(enabled=False)
        kb = _skill_detail_keyboard(skill)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Enable" in t for t in texts)

    def test_rejected_action(self):
        skill = _make_skill(review_status="rejected", enabled=False)
        kb = _skill_detail_keyboard(skill)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert any("Re-approve" in t for t in texts)


# ── Tests: /skills command ───────────────────────────────────────────


class TestCmdSkills:
    @pytest.mark.asyncio
    async def test_no_args_shows_panel(self):
        msg = _make_message()
        _owner, patches = _mock_session_context()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_data.list_skills",
                AsyncMock(return_value=[_make_skill()]),
            ),
        ):
            await cmd_skills(msg, CommandObject(command="skills", prefix="/"))
        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "<b>Skills</b>" in text

    @pytest.mark.asyncio
    async def test_empty_shows_empty_panel(self):
        msg = _make_message()
        _owner, patches = _mock_session_context()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_data.list_skills",
                AsyncMock(return_value=[]),
            ),
        ):
            await cmd_skills(msg, CommandObject(command="skills", prefix="/"))
        text = msg.answer.call_args[0][0]
        assert "Skills пока пусты" in text

    @pytest.mark.asyncio
    async def test_legacy_show_subcommand(self):
        msg = _make_message(text="/skills show my-skill")
        _owner, patches = _mock_session_context()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_cmd.get_skill_by_name",
                AsyncMock(return_value=_make_skill()),
            ),
        ):
            await cmd_skills(
                msg, CommandObject(command="skills", prefix="/", args="show my-skill")
            )
        text = msg.answer.call_args[0][0]
        assert "test-skill" in text


# ── Tests: callback handlers ─────────────────────────────────────────


class TestCbSkillsPage:
    @pytest.mark.asyncio
    async def test_renders_page(self):
        cb = _make_callback(data="skills:page:all:0")
        _owner, patches = _mock_session_context()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_data.list_skills",
                AsyncMock(return_value=[_make_skill()]),
            ),
        ):
            await cb_skills_page(cb)
        cb.message.edit_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_page_defaults_to_zero(self):
        cb = _make_callback(data="skills:page:all:bad")
        _owner, patches = _mock_session_context()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_data.list_skills",
                AsyncMock(return_value=[]),
            ),
        ):
            await cb_skills_page(cb)
        cb.answer.assert_called_once()


class TestCbSkillDetail:
    @pytest.mark.asyncio
    async def test_renders_detail(self):
        cb = _make_callback(data="skills:detail:1")
        _owner, patches = _mock_session_context()
        skill = _make_skill(skill_id=1)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_callbacks._get_skill_by_id",
                AsyncMock(return_value=skill),
            ),
        ):
            await cb_skill_detail(cb)
        cb.message.edit_text.assert_called_once()
        call_text = cb.message.edit_text.call_args[0][0]
        assert "test-skill" in call_text


class TestCbSkillToggle:
    @pytest.mark.asyncio
    async def test_disables_active_skill(self):
        cb = _make_callback(data="skills:toggle:1")
        _owner, patches = _mock_session_context()
        skill = _make_skill(skill_id=1)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_callbacks._get_skill_by_id",
                AsyncMock(return_value=skill),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.set_skill_enabled",
                AsyncMock(return_value=skill),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.cb_skill_detail", AsyncMock()
            ) as mock_detail,
        ):
            await cb_skill_toggle(cb)
        cb.answer.assert_called_once()
        mock_detail.assert_called_once()


class TestCbSkillApprove:
    @pytest.mark.asyncio
    async def test_approve_success(self):
        cb = _make_callback(data="skills:approve:1")
        _owner, patches = _mock_session_context()
        skill = _make_skill(review_status="proposed", skill_id=1)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_callbacks._get_skill_by_id",
                AsyncMock(return_value=skill),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.approve_skill",
                AsyncMock(return_value=True),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.cb_skill_detail", AsyncMock()
            ) as mock_detail,
        ):
            await cb_skill_approve(cb)
        cb.answer.assert_called_once()
        assert "одобрен" in cb.answer.call_args[0][0]
        mock_detail.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_failure(self):
        cb = _make_callback(data="skills:approve:1")
        _owner, patches = _mock_session_context()
        skill = _make_skill(review_status="proposed", skill_id=1)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_callbacks._get_skill_by_id",
                AsyncMock(return_value=skill),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.approve_skill",
                AsyncMock(return_value=False),
            ),
        ):
            await cb_skill_approve(cb)
        cb.answer.assert_called_once()
        assert "Не удалось" in cb.answer.call_args[0][0]  # noqa: RUF001


class TestCbSkillReject:
    @pytest.mark.asyncio
    async def test_reject_success(self):
        cb = _make_callback(data="skills:reject:1")
        _owner, patches = _mock_session_context()
        skill = _make_skill(review_status="proposed", skill_id=1)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_callbacks._get_skill_by_id",
                AsyncMock(return_value=skill),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.reject_skill",
                AsyncMock(return_value=True),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.cb_skill_detail", AsyncMock()
            ) as mock_detail,
        ):
            await cb_skill_reject(cb)
        cb.answer.assert_called_once()
        assert "отклонён" in cb.answer.call_args[0][0]
        mock_detail.assert_called_once()


class TestCbSkillPromote:
    @pytest.mark.asyncio
    async def test_promote_success(self):
        cb = _make_callback(data="skills:promote:1")
        _owner, patches = _mock_session_context()
        skill = _make_skill(skill_id=1)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_callbacks._get_skill_by_id",
                AsyncMock(return_value=skill),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.promote_to_global",
                AsyncMock(return_value=True),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.cb_skill_detail", AsyncMock()
            ) as mock_detail,
        ):
            await cb_skill_promote(cb)
        cb.answer.assert_called_once()
        assert "promoted" in cb.answer.call_args[0][0]
        mock_detail.assert_called_once()

    @pytest.mark.asyncio
    async def test_promote_exception(self):
        cb = _make_callback(data="skills:promote:1")
        _owner, patches = _mock_session_context()
        skill = _make_skill(skill_id=1)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_callbacks._get_skill_by_id",
                AsyncMock(return_value=skill),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.promote_to_global",
                AsyncMock(side_effect=RuntimeError("db fail")),
            ),
        ):
            await cb_skill_promote(cb)
        cb.answer.assert_called_once()
        assert "Не удалось" in cb.answer.call_args[0][0]  # noqa: RUF001


class TestCbSkillRollback:
    @pytest.mark.asyncio
    async def test_rollback_updates_body(self):
        cb = _make_callback(data="skills:rollback:1")
        _owner, patches = _mock_session_context()
        skill = _make_skill(skill_id=1, best_body="old-body", version="1.2.0")
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_callbacks._get_skill_by_id",
                AsyncMock(return_value=skill),
            ),
            patch(
                "src.bot.handlers.skills_callbacks.cb_skill_detail", AsyncMock()
            ) as mock_detail,
        ):
            await cb_skill_rollback(cb)
        assert skill.body == "old-body"
        assert skill.version == "1.3.0"
        cb.answer.assert_called_once()
        mock_detail.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_without_best_body_warns(self):
        cb = _make_callback(data="skills:rollback:1")
        _owner, patches = _mock_session_context()
        skill = _make_skill(skill_id=1, best_body=None)
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patch(
                "src.bot.handlers.skills_callbacks._get_skill_by_id",
                AsyncMock(return_value=skill),
            ),
        ):
            await cb_skill_rollback(cb)
        cb.answer.assert_called_once()
        assert "Нет стабильной" in cb.answer.call_args[0][0]


class TestCbSkillsEvolve:
    @pytest.mark.asyncio
    async def test_dryrun_reports_candidates(self):
        cb = _make_callback(data="skills:evolve_dryrun:0")
        skill = _make_skill()
        with patch(
            "src.bot.handlers.skills_callbacks.find_underperforming_skills",
            AsyncMock(return_value=[skill]),
        ):
            await cb_skills_evolve_dryrun(cb)
        cb.message.edit_text.assert_called_once()
        text = cb.message.edit_text.call_args[0][0]
        assert "Кандидатов: 1" in text

    @pytest.mark.asyncio
    async def test_dryrun_failure(self):
        cb = _make_callback(data="skills:evolve_dryrun:0")
        with patch(
            "src.bot.handlers.skills_callbacks.find_underperforming_skills",
            AsyncMock(side_effect=RuntimeError("db fail")),
        ):
            await cb_skills_evolve_dryrun(cb)
        cb.answer.assert_called_once_with(
            "⚠️ Не удалось найти кандидатов",  # noqa: RUF001
            show_alert=True,
        )

    @pytest.mark.asyncio
    async def test_apply_reports_results(self):
        """Apply callback runs evolution and reports counts."""
        cb = _make_callback(data="skills:evolve_apply:0")
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
        cb.message.edit_text.assert_called_once()
        text = cb.message.edit_text.call_args[0][0]
        assert "Применено: 1" in text
        assert "test-skill" in text


class TestCbSkillsStats:
    @pytest.mark.asyncio
    async def test_shows_stats(self):
        cb = _make_callback(data="skills:stats:0")
        with patch(
            "src.bot.handlers.skills_callbacks.curator_stats",
            AsyncMock(
                return_value={
                    "proposed": 1,
                    "approved": 2,
                    "rejected": 3,
                    "global": 4,
                    "total": 6,
                }
            ),
        ):
            await cb_skills_stats(cb)
        cb.message.edit_text.assert_called_once()
        text = cb.message.edit_text.call_args[0][0]
        assert "Активен: 2" in text
        assert "Global: 4" in text

    @pytest.mark.asyncio
    async def test_stats_failure(self):
        cb = _make_callback(data="skills:stats:0")
        with patch(
            "src.bot.handlers.skills_callbacks.curator_stats",
            AsyncMock(side_effect=RuntimeError("db fail")),
        ):
            await cb_skills_stats(cb)
        cb.answer.assert_called_once()
        assert "Не удалось" in cb.answer.call_args[0][0]  # noqa: RUF001


# ── Tests: D5 regression fixes ───────────────────────────────────────


class TestParseCallbackSkillId:
    def test_zero_id_returns_none(self):
        cb = _make_callback(data="skills:detail:0")
        assert _parse_callback_skill_id(cb) is None

    def test_negative_id_returns_none(self):
        cb = _make_callback(data="skills:detail:-3")
        assert _parse_callback_skill_id(cb) is None

    def test_malformed_callback_returns_none(self):
        cb = _make_callback(data="skills:detail")
        assert _parse_callback_skill_id(cb) is None

    def test_positive_id_parsed(self):
        cb = _make_callback(data="skills:detail:42")
        assert _parse_callback_skill_id(cb) == 42


class TestStatusFilterClauses:
    def test_stale_and_archived_filters_are_identical(self):
        """Stale and archived now use identical SQL (split done in Python)."""
        owner = MagicMock()
        owner.id = 1
        stale = _status_filter_clauses("stale", owner)
        archived = _status_filter_clauses("archived", owner)
        stale_sql = str(
            select(1).where(*stale).compile(compile_kwargs={"literal_binds": True})
        )
        archived_sql = str(
            select(1).where(*archived).compile(compile_kwargs={"literal_binds": True})
        )
        assert stale_sql == archived_sql

    def test_stale_is_just_disabled_clauses(self):
        """Stale no longer includes LIKE decay markers — uses Python filtering."""
        owner = MagicMock()
        owner.id = 1
        clauses = _status_filter_clauses("stale", owner)
        compiled = str(
            select(1).where(*clauses).compile(compile_kwargs={"literal_binds": True})
        ).lower()
        # No LIKE scan on description
        assert _DECAY_MARKER.lower() not in compiled
        assert _SUCCESS_RATE_MARKER.lower() not in compiled
        # Still has disabled + approved filter
        assert "enabled" in compiled
        assert "approved" in compiled

    def test_archived_is_just_disabled_clauses(self):
        """Archived no longer includes NOT(decay) — uses Python filtering."""
        owner = MagicMock()
        owner.id = 1
        clauses = _status_filter_clauses("archived", owner)
        compiled = str(
            select(1).where(*clauses).compile(compile_kwargs={"literal_binds": True})
        ).lower()
        assert _DECAY_MARKER.lower() not in compiled
        assert "not" not in compiled  # No NOT(decay_marker) clause
        assert "enabled" in compiled

    def test_python_split_stale_vs_archived(self):
        """Verify Python-level split via _is_stale correctly separates skills."""
        stale_skill = _make_skill(
            review_status="approved",
            enabled=False,
            description="[DECAYED] low success_rate",
            skill_id=1,
        )
        archived_skill = _make_skill(
            review_status="approved",
            enabled=False,
            description="",
            skill_id=2,
        )
        assert _is_stale(stale_skill)
        assert not _is_stale(archived_skill)


# ── Tests: skill detail formatting ───────────────────────────────────


class TestFormatSkillDetail:
    def test_contains_status_and_metrics(self):
        skill = _make_skill(
            success_count=5,
            failure_count=1,
            validation_score=0.9,
            version="2.0.0",
        )
        text = _format_skill_detail(skill)
        assert "v2.0.0" in text
        assert "Активен" in text
        assert "score:90%" in text

    def test_no_validation_score(self):
        skill = _make_skill()
        text = _format_skill_detail(skill)
        assert "score:—" in text
