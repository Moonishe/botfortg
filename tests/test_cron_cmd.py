"""Unit tests for Telegram Cron Panel — src/bot/handlers/cron_cmd.py.

Covers:
  - /cron list/add/blueprints entry points
  - Inline callbacks: show, toggle, run, delete, blueprint
  - Destructive actions route through Approval Kernel
  - Intent handlers exec_cron_run / exec_cron_delete
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.filters import CommandObject
from aiogram.types import Message

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"  # noqa: S105
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.bot.handlers import cron_cmd
from src.bot.handlers.cron_exec import exec_cron_delete, exec_cron_run

OWNER_TG_ID = 123456789


# ── Helpers ─────────────────────────────────────────────────────────


def _make_owner() -> MagicMock:
    owner = MagicMock()
    owner.id = 1
    owner.telegram_id = OWNER_TG_ID
    return owner


def _make_message(user_id: int = OWNER_TG_ID, text: str = "/cron") -> MagicMock:
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _make_callback(user_id: int = OWNER_TG_ID, data: str = "cron:show:1") -> MagicMock:
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.data = data
    cb.message = MagicMock(spec=Message)
    cb.message.from_user = MagicMock()
    cb.message.from_user.id = user_id
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _make_job(
    job_id: int = 1,
    name: str = "test-job",
    enabled: bool = True,
    payload_type: str = "message",
    payload: str | None = None,
    user_id: int = 1,
) -> MagicMock:
    job = MagicMock()
    job.id = job_id
    job.name = name
    job.enabled = enabled
    job.cron_expression = "0 9 * * *"
    job.payload_type = payload_type
    job.payload = payload or '{"text": "hello"}'
    job.channel = "notification_queue"
    job.user_id = user_id
    job.run_count = 0
    job.last_run_at = None
    job.next_run_at = datetime.now(UTC)
    job.tags = None
    job.description = ""
    job.timezone = "UTC"
    return job


def _mock_session():
    """Patch get_session and get_or_create_user for cron_cmd handlers."""
    owner = _make_owner()

    class _FakeSession:
        async def __aenter__(self):
            session = MagicMock()
            session.flush = AsyncMock()
            session.commit = AsyncMock()
            session.refresh = AsyncMock()
            return session

        async def __aexit__(self, *args):
            return False

    return owner, _FakeSession


# ── Tests: entry point /cron ─────────────────────────────────────


@pytest.mark.asyncio
async def test_cron_empty_list():
    """No jobs → empty list with blueprints button."""
    msg = _make_message()
    owner, _FakeSession = _mock_session()

    with (
        patch("src.bot.handlers.cron_cmd.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_cmd.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_cmd.list_user_jobs",
            AsyncMock(return_value=[]),
        ),
    ):
        await cron_cmd.cron_cmd(msg, CommandObject(command="cron", args=""))

    assert msg.answer.called
    text, kwargs = msg.answer.call_args
    assert "cron" in text[0].lower()
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_cron_list_with_jobs():
    """List shows jobs and inline buttons."""
    msg = _make_message()
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=7, name="Утро")

    with (
        patch("src.bot.handlers.cron_cmd.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_cmd.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_cmd.list_user_jobs",
            AsyncMock(return_value=[job]),
        ),
    ):
        await cron_cmd.cron_cmd(msg, CommandObject(command="cron", args=""))

    text, kwargs = msg.answer.call_args
    assert "#7" in text[0]
    assert "Утро" in text[0]
    markup = kwargs["reply_markup"]
    assert markup.inline_keyboard
    row = markup.inline_keyboard[0]
    assert any("cron:toggle:7" in btn.callback_data for btn in row)
    assert any("cron:run:7" in btn.callback_data for btn in row)
    assert any("cron:delete:7" in btn.callback_data for btn in row)
    assert any("cron:show:7" in btn.callback_data for btn in row)


@pytest.mark.asyncio
async def test_cron_add_quick_create():
    """'/cron add <name> <expr> <type> <payload>' creates a job."""
    msg = _make_message(text='/cron add Morning 0 9 * * * message {"text":"hi"}')
    owner, _FakeSession = _mock_session()
    created = _make_job(job_id=3, name="Morning")

    with (
        patch("src.bot.handlers.cron_cmd.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_cmd.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_cmd.create_cron_job",
            AsyncMock(return_value=created),
        ),
        patch(
            "src.bot.handlers.cron_cmd.validate_cron",
            return_value=True,
        ),
    ):
        await cron_cmd.cron_cmd(msg, CommandObject(command="cron", args=msg.text[6:]))

    text, _ = msg.answer.call_args
    assert "Создана задача" in text[0]
    assert "#3" in text[0]


@pytest.mark.asyncio
async def test_cron_add_invalid_payload():
    """Invalid JSON payload returns error without touching DB."""
    msg = _make_message(text="/cron add Morning 0 9 * * * message notjson")
    _owner, _FakeSession = _mock_session()

    with patch("src.bot.handlers.cron_cmd.get_session", _FakeSession):
        await cron_cmd.cron_cmd(msg, CommandObject(command="cron", args=msg.text[6:]))

    text, _ = msg.answer.call_args
    assert "payload должен быть валидным JSON" in text[0]


@pytest.mark.asyncio
async def test_cron_blueprints():
    """'/cron blueprints' shows built-in blueprints."""
    msg = _make_message(text="/cron blueprints")
    _owner, _FakeSession = _mock_session()

    with patch("src.bot.handlers.cron_cmd.get_session", _FakeSession):
        await cron_cmd.cron_cmd(msg, CommandObject(command="cron", args="blueprints"))

    text, kwargs = msg.answer.call_args
    assert "Готовые шаблоны" in text[0]
    assert "reply_markup" in kwargs


# ── Tests: inline callbacks ──────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_show():
    """Show callback renders job details."""
    cb = _make_callback(data="cron:show:5")
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=5, name="Детали")

    with (
        patch("src.bot.handlers.cron_cmd.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_cmd.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_cmd.get_cron_job",
            AsyncMock(return_value=job),
        ),
    ):
        await cron_cmd._cb_show(cb)

    assert cb.message.edit_text.called
    text, _ = cb.message.edit_text.call_args
    assert "#5" in text[0]
    assert "Детали" in text[0]


@pytest.mark.asyncio
async def test_cb_show_wrong_owner():
    """Show callback for another user's job is rejected."""
    cb = _make_callback(data="cron:show:5")
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=5, user_id=999)

    with (
        patch("src.bot.handlers.cron_cmd.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_cmd.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_cmd.get_cron_job",
            AsyncMock(return_value=job),
        ),
    ):
        await cron_cmd._cb_show(cb)

    assert cb.answer.called
    assert "Задача не найдена" in cb.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_cb_toggle():
    """Toggle callback flips enabled and refreshes list."""
    cb = _make_callback(data="cron:toggle:2")
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=2, enabled=True)
    updated = _make_job(job_id=2, enabled=False)

    with (
        patch("src.bot.handlers.cron_cmd.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_cmd.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_cmd.get_cron_job",
            AsyncMock(return_value=job),
        ),
        patch(
            "src.bot.handlers.cron_cmd.update_cron_job",
            AsyncMock(return_value=updated),
        ),
        patch(
            "src.bot.handlers.cron_cmd.list_user_jobs",
            AsyncMock(return_value=[updated]),
        ),
    ):
        await cron_cmd._cb_toggle(cb)

    cb.answer.assert_called_once()
    assert "Отключена" in cb.answer.call_args[0][0]
    assert cb.message.edit_text.called


@pytest.mark.asyncio
async def test_cb_run_approval_kernel():
    """Run callback creates high-risk Approval Kernel confirmation."""
    cb = _make_callback(data="cron:run:4")
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=4, name="Запуск")

    confirm_cb = "ap:intent:42:abc"
    cancel_cb = "ap:intent:42:cancel"

    with (
        patch("src.bot.handlers.cron_cmd.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_cmd.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_cmd.get_cron_job",
            AsyncMock(return_value=job),
        ),
        patch(
            "src.bot.handlers.cron_cmd._store_intent_confirmation",
            AsyncMock(return_value=(confirm_cb, cancel_cb)),
        ) as store_mock,
    ):
        await cron_cmd._cb_run(cb)

    store_mock.assert_called_once()
    call = store_mock.call_args.kwargs
    assert call["intent_name"] == "cron_run"
    assert call["risk"] == "high"
    assert call["intent"]["job_id"] == 4
    assert call["intent"]["user_id"] == OWNER_TG_ID
    kwargs = cb.message.edit_text.call_args.kwargs
    assert "Подтвердить" in str(kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_cb_delete_approval_kernel():
    """Delete callback creates high-risk Approval Kernel confirmation."""
    cb = _make_callback(data="cron:delete:4")
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=4, name="Удаление")

    confirm_cb = "ap:intent:42:abc"
    cancel_cb = "ap:intent:42:cancel"

    with (
        patch("src.bot.handlers.cron_cmd.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_cmd.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_cmd.get_cron_job",
            AsyncMock(return_value=job),
        ),
        patch(
            "src.bot.handlers.cron_cmd._store_intent_confirmation",
            AsyncMock(return_value=(confirm_cb, cancel_cb)),
        ) as store_mock,
    ):
        await cron_cmd._cb_delete(cb)

    store_mock.assert_called_once()
    call = store_mock.call_args.kwargs
    assert call["intent_name"] == "cron_delete"
    assert call["risk"] == "high"
    assert call["intent"]["user_id"] == OWNER_TG_ID


@pytest.mark.asyncio
async def test_cb_blueprint():
    """Blueprint callback creates a job from template and refreshes list."""
    cb = _make_callback(data="cron:blueprint:Утренняя сводка")
    owner, _FakeSession = _mock_session()
    created = _make_job(job_id=9, name="Утренняя сводка")

    with (
        patch("src.bot.handlers.cron_cmd.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_cmd.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_cmd.create_cron_job",
            AsyncMock(return_value=created),
        ),
        patch(
            "src.bot.handlers.cron_cmd.list_user_jobs",
            AsyncMock(return_value=[created]),
        ),
    ):
        await cron_cmd._cb_blueprint(cb)

    assert cb.answer.called
    assert cb.message.edit_text.called


# ── Tests: intent handlers ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_exec_cron_run_success():
    """exec_cron_run dispatches a job and returns ok."""
    msg = _make_message()
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=10, payload_type="message")

    with (
        patch("src.bot.handlers.cron_exec.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_exec.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_exec.get_cron_job",
            AsyncMock(return_value=job),
        ),
        patch(
            "src.bot.handlers.cron_exec.dispatch_cron_job",
            AsyncMock(return_value={"success": True, "output": "done"}),
        ),
    ):
        result = await exec_cron_run({"job_id": 10}, msg)

    assert result["ok"] is True
    msg.answer.assert_called_once()


@pytest.mark.asyncio
async def test_exec_cron_run_progress_llm_prompt():
    """llm_prompt jobs show a progress card that is deleted afterwards."""
    msg = _make_message()
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=11, payload_type="llm_prompt", payload='{"prompt":"hi"}')
    progress_msg = MagicMock()
    progress_msg.delete = AsyncMock()
    msg.answer = AsyncMock(return_value=progress_msg)

    with (
        patch("src.bot.handlers.cron_exec.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_exec.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_exec.get_cron_job",
            AsyncMock(return_value=job),
        ),
        patch(
            "src.bot.handlers.cron_exec.dispatch_cron_job",
            AsyncMock(return_value={"success": True, "output": "done"}),
        ),
    ):
        result = await exec_cron_run({"job_id": 11}, msg)

    assert result["ok"] is True
    assert (
        msg.answer.call_args_list[0][0][0] == "⏳ Генерирую LLM-ответ для cron-задачи…"
    )
    progress_msg.delete.assert_called_once()


@pytest.mark.asyncio
async def test_exec_cron_delete_success():
    """exec_cron_delete removes owned job."""
    msg = _make_message()
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=12)

    with (
        patch("src.bot.handlers.cron_exec.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_exec.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_exec.get_cron_job",
            AsyncMock(return_value=job),
        ),
        patch(
            "src.bot.handlers.cron_exec.delete_cron_job",
            AsyncMock(return_value=True),
        ),
    ):
        result = await exec_cron_delete({"job_id": 12}, msg)

    assert result["ok"] is True
    msg.answer.assert_called_once()


@pytest.mark.asyncio
async def test_exec_cron_run_wrong_owner():
    """Running another user's job is rejected."""
    msg = _make_message()
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=13, user_id=999)

    with (
        patch("src.bot.handlers.cron_exec.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_exec.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_exec.get_cron_job",
            AsyncMock(return_value=job),
        ),
    ):
        result = await exec_cron_run({"job_id": 13}, msg)

    assert result["ok"] is False
    assert "Задача не найдена" in result["error"]


@pytest.mark.asyncio
async def test_exec_cron_run_dispatch_error():
    """Dispatch failure returns error."""
    msg = _make_message()
    owner, _FakeSession = _mock_session()
    job = _make_job(job_id=14, payload_type="message")

    with (
        patch("src.bot.handlers.cron_exec.get_session", _FakeSession),
        patch(
            "src.bot.handlers.cron_exec.get_or_create_user",
            AsyncMock(return_value=owner),
        ),
        patch(
            "src.bot.handlers.cron_exec.get_cron_job",
            AsyncMock(return_value=job),
        ),
        patch(
            "src.bot.handlers.cron_exec.dispatch_cron_job",
            AsyncMock(return_value={"success": False, "output": "boom"}),
        ),
    ):
        result = await exec_cron_run({"job_id": 14}, msg)

    assert result["ok"] is False
    assert "boom" in result["error"]
