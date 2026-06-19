"""Интеграционные тесты success-path для callback-хендлеров Deep Research.

Проверяет:
- cb_research_view: редактирует сообщение — сводка + inline-кнопки.
- cb_research_file: отправка файла отчёта через FSInputFile.
- cb_research_dig_deeper: отправка follow-up запросов.
- cb_research_retry: перезапуск исследования через pipeline.submit.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message


@pytest.fixture(autouse=True)
def _reset_engine_pool() -> None:
    """Dispose stale connections before each test (from conftest)."""
    try:
        from src.db.session import engine

        engine.sync_engine.dispose()
    except Exception:
        pass  # ponytail: engine may not be initialised in DB-mocked tests


@pytest.fixture(autouse=True)
def _patch_send_rich_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub send_rich_message to avoid network calls and unawaited AsyncMock warnings.

    Tests in this module exercise the fallback edit_text/answer_document path.
    """
    monkeypatch.setattr(
        "src.bot.rich_messages.send_rich_message",
        AsyncMock(return_value=None),
    )


# ── Вспомогательные фабрики ──────────────────────────────────────────


def _make_callback_query(
    data: str,
    from_user_id: int = 123456789,
    message: MagicMock | None = None,
) -> MagicMock:
    """Создать мок CallbackQuery с указанными callback_data.

    Для success-path тестов message будет заменён на патч isinstance.
    """
    if message is None:
        message = MagicMock()
        message.delete = AsyncMock()
        message.edit_text = AsyncMock()
        message.answer_document = AsyncMock()
        message.answer = AsyncMock()

    user = MagicMock()
    user.id = from_user_id

    cb = MagicMock()
    cb.data = data
    cb.from_user = user
    cb.message = message
    cb.answer = AsyncMock()
    return cb


def _make_research_result(
    job_id: str = "abc123",
    query: str = "Test query",
    status: str = "completed",
    summary: str = "Test summary content",
) -> MagicMock:
    """Создать мок ResearchResult."""
    from src.core.rag.types import ResearchStatus

    result = MagicMock()
    result.job_id = job_id
    result.query = query
    result.status = ResearchStatus(status)
    result.summary = summary
    return result


def _patch_safe_resolve(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Подменить _safe_resolve в research_cb — резолвит пути в tmp_dir."""

    def _fake_safe_resolve(raw: str) -> Path | None:
        normalised = raw.replace("/", os.sep).replace("\\", os.sep)
        return tmp_dir / normalised

    monkeypatch.setattr(
        "src.bot.handlers.research_cb._safe_resolve",
        _fake_safe_resolve,
    )


def _fake_isinstance_factory(cb_message: MagicMock):
    """Фабрика для патча isinstance — MagicMock message проходит проверку Message."""

    def _fake_isinstance(obj: object, cls: type) -> bool:
        if cls is Message and obj is cb_message:
            return True
        import builtins

        return builtins.isinstance(obj, cls)

    return _fake_isinstance


# ── cb_research_view: success-path (новый UX) ─────────────────────────


class TestCbResearchViewSuccess:
    """Интеграционные тесты нового поведения cb_research_view.

    Теперь хендлер редактирует сообщение: показывает сводку + inline-кнопки,
    а файл отправляется отдельным хендлером cb_research_file.
    """

    @pytest.mark.asyncio
    async def test_edits_message_with_summary_and_buttons(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Success-path: SUMMARY.md существует → edit_text со сводкой и кнопками."""
        from src.bot.handlers.research_cb import cb_research_view

        cb = _make_callback_query("research:view:abc123")
        mock_result = _make_research_result(
            "abc123", "Как работает Python GIL?", summary="Summary from disk"
        )

        # Создаём временную директорию и SUMMARY.md
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            research_dir = tmp_dir / "data" / "research" / "abc123"
            research_dir.mkdir(parents=True)
            summary_file = research_dir / "SUMMARY.md"
            summary_file.write_text("Полный отчёт исследования", encoding="utf-8")

            _patch_safe_resolve(monkeypatch, tmp_dir)

            with (
                patch(
                    "src.bot.handlers.research_cb.get_deep_research_pipeline",
                ) as mock_get_pipeline,
                patch(
                    "src.bot.handlers.research_cb.isinstance",
                    side_effect=_fake_isinstance_factory(cb.message),
                ),
            ):
                mock_pipeline = MagicMock()
                mock_pipeline.get_status = AsyncMock(return_value=mock_result)
                mock_get_pipeline.return_value = mock_pipeline

                await cb_research_view(cb)

                # Проверка: edit_text вызван со сводкой
                cb.message.edit_text.assert_called_once()
                call_kwargs = cb.message.edit_text.call_args[1]
                text_arg = cb.message.edit_text.call_args[0][0]
                assert "Полный отчёт исследования" in text_arg, (
                    f"Expected summary in message, got: {text_arg}"
                )
                assert "📋" in text_arg, f"Expected result header, got: {text_arg}"

                # Проверка: reply_markup содержит 3 кнопки
                reply_markup = call_kwargs.get("reply_markup")
                assert reply_markup is not None, "Expected reply_markup with buttons"
                if hasattr(reply_markup, "inline_keyboard"):
                    rows = reply_markup.inline_keyboard
                else:
                    rows = reply_markup.get("inline_keyboard", [])
                assert len(rows) == 3, f"Expected 3 button rows, got {len(rows)}"

                cb.answer.assert_any_call("✅ Отчёт загружен!")

    @pytest.mark.asyncio
    async def test_edits_message_with_fallback_summary_when_file_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Success-path: SUMMARY.md не существует → edit_text с in-memory сводкой."""
        from src.bot.handlers.research_cb import cb_research_view

        cb = _make_callback_query("research:view:abc123")
        mock_result = _make_research_result(
            "abc123", "Test query", summary="In-memory summary"
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _patch_safe_resolve(monkeypatch, tmp_dir)

            with (
                patch(
                    "src.bot.handlers.research_cb.get_deep_research_pipeline",
                ) as mock_get_pipeline,
                patch(
                    "src.bot.handlers.research_cb.isinstance",
                    side_effect=_fake_isinstance_factory(cb.message),
                ),
            ):
                mock_pipeline = MagicMock()
                mock_pipeline.get_status = AsyncMock(return_value=mock_result)
                mock_get_pipeline.return_value = mock_pipeline

                await cb_research_view(cb)

                cb.message.edit_text.assert_called_once()
                text_arg = cb.message.edit_text.call_args[0][0]
                assert "In-memory summary" in text_arg, (
                    f"Expected in-memory summary, got: {text_arg}"
                )

                call_kwargs = cb.message.edit_text.call_args[1]
                reply_markup = call_kwargs.get("reply_markup")
                assert reply_markup is not None

                cb.answer.assert_any_call("✅ Отчёт загружен!")


# ── cb_research_file: success-path ────────────────────────────────────


class TestCbResearchFileSuccess:
    """Интеграционные тесты отправки файла отчёта."""

    @pytest.mark.asyncio
    async def test_sends_report_file_when_summary_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Success-path: SUMMARY.md существует → answer_document с FSInputFile."""
        from src.bot.handlers.research_cb import cb_research_file

        cb = _make_callback_query("research:file:abc123")
        mock_result = _make_research_result(
            "abc123", "Как работает Python GIL?", summary="Summary from disk"
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            research_dir = tmp_dir / "data" / "research" / "abc123"
            research_dir.mkdir(parents=True)
            summary_file = research_dir / "SUMMARY.md"
            summary_file.write_text("Полный отчёт исследования", encoding="utf-8")

            _patch_safe_resolve(monkeypatch, tmp_dir)

            with (
                patch(
                    "src.bot.handlers.research_cb.get_deep_research_pipeline",
                ) as mock_get_pipeline,
                patch(
                    "src.bot.handlers.research_cb.FSInputFile",
                ) as mock_fs_input,
                patch(
                    "src.bot.handlers.research_cb.isinstance",
                    side_effect=_fake_isinstance_factory(cb.message),
                ),
            ):
                mock_pipeline = MagicMock()
                mock_pipeline.get_status = AsyncMock(return_value=mock_result)
                mock_get_pipeline.return_value = mock_pipeline

                await cb_research_file(cb)

                mock_fs_input.assert_called_once()
                call_args = mock_fs_input.call_args
                assert call_args is not None
                file_path_arg = str(call_args[0][0])
                assert "abc123" in file_path_arg or "SUMMARY" in file_path_arg, (
                    f"Expected path to contain job_id, got: {file_path_arg}"
                )

                cb.message.answer_document.assert_called_once()
                cb.answer.assert_any_call("✅ Файл отправлен!")

    @pytest.mark.asyncio
    async def test_sends_report_via_buffered_when_file_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Success-path: SUMMARY.md не существует → BufferedInputFile."""
        from src.bot.handlers.research_cb import cb_research_file

        cb = _make_callback_query("research:file:abc123")
        mock_result = _make_research_result(
            "abc123", "Test query", summary="In-memory summary"
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _patch_safe_resolve(monkeypatch, tmp_dir)

            with (
                patch(
                    "src.bot.handlers.research_cb.get_deep_research_pipeline",
                ) as mock_get_pipeline,
                patch(
                    "src.bot.handlers.research_cb.BufferedInputFile",
                ) as mock_buffered,
                patch(
                    "src.bot.handlers.research_cb.isinstance",
                    side_effect=_fake_isinstance_factory(cb.message),
                ),
            ):
                mock_pipeline = MagicMock()
                mock_pipeline.get_status = AsyncMock(return_value=mock_result)
                mock_get_pipeline.return_value = mock_pipeline

                await cb_research_file(cb)

                mock_buffered.assert_called_once()
                cb.message.answer_document.assert_called_once()
                cb.answer.assert_any_call("✅ Файл отправлен!")


# ── cb_research_dig_deeper: success-path ─────────────────────────────


class TestCbResearchDigDeeperSuccess:
    """Интеграционные тесты генерации follow-up запросов."""

    @pytest.mark.asyncio
    async def test_sends_followup_suggestions(self) -> None:
        """Success-path: отправляет 3 команды /research с follow-up запросами."""
        from src.bot.handlers.research_cb import cb_research_dig_deeper

        cb = _make_callback_query("research:dig_deeper:abc123")
        mock_result = _make_research_result(
            "abc123", "Как работает Python GIL?", summary="..."
        )

        with (
            patch(
                "src.bot.handlers.research_cb.get_deep_research_pipeline",
            ) as mock_get_pipeline,
            patch(
                "src.bot.handlers.research_cb.isinstance",
                side_effect=_fake_isinstance_factory(cb.message),
            ),
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.get_status = AsyncMock(return_value=mock_result)
            mock_get_pipeline.return_value = mock_pipeline

            await cb_research_dig_deeper(cb)

            cb.message.answer.assert_called_once()
            answer_text = cb.message.answer.call_args[0][0]
            assert "/research" in answer_text
            assert answer_text.count("/research") == 3, (
                f"Expected 3 /research commands, got: {answer_text}"
            )
            assert "Python GIL" in answer_text or ("Расскажи подробнее" in answer_text)

            cb.answer.assert_any_call("✅ Идеи отправлены!")


# ── cb_research_retry: success-path ───────────────────────────────────


class TestCbResearchRetrySuccess:
    """Интеграционные тесты успешного перезапуска исследования."""

    @pytest.mark.asyncio
    async def test_retry_submits_new_job(self) -> None:
        """Success-path: перезапуск — pipeline.submit вызван с ResearchRequest."""
        from src.bot.handlers.research_cb import cb_research_retry

        cb = _make_callback_query("research:retry:abc123")
        mock_result = _make_research_result("abc123", "Как работает Python GIL?")

        with (
            patch(
                "src.bot.handlers.research_cb.get_deep_research_pipeline",
            ) as mock_get_pipeline,
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.get_status = AsyncMock(return_value=mock_result)
            mock_pipeline.submit = MagicMock(return_value="newjob456def")
            mock_get_pipeline.return_value = mock_pipeline

            await cb_research_retry(cb)

            mock_pipeline.submit.assert_called_once()
            submit_call = mock_pipeline.submit.call_args[0][0]
            assert submit_call.query == "Как работает Python GIL?"
            assert submit_call.max_minutes == 5

            cb.message.answer.assert_called_once()
            answer_text = cb.message.answer.call_args[0][0]
            assert "newjob456def" in answer_text
            assert "перезапущено" in answer_text.lower()

            cb.answer.assert_any_call("✅ Перезапущено!")

    @pytest.mark.asyncio
    async def test_retry_uses_original_query_from_result(self) -> None:
        """Перезапуск использует query из оригинального ResearchResult."""
        from src.bot.handlers.research_cb import cb_research_retry

        cb = _make_callback_query("research:retry:orig123")
        mock_result = _make_research_result(
            "orig123",
            "Оригинальный запрос про квантовые вычисления",
        )
        mock_result.query = "Оригинальный запрос про квантовые вычисления"

        with (
            patch(
                "src.bot.handlers.research_cb.get_deep_research_pipeline",
            ) as mock_get_pipeline,
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.get_status = AsyncMock(return_value=mock_result)
            mock_pipeline.submit = MagicMock(return_value="new_job_xyz")
            mock_get_pipeline.return_value = mock_pipeline

            await cb_research_retry(cb)

            submit_call = mock_pipeline.submit.call_args[0][0]
            assert submit_call.query == "Оригинальный запрос про квантовые вычисления"

    @pytest.mark.asyncio
    async def test_retry_logs_exception_and_alerts_user(self) -> None:
        """Если pipeline.submit бросает исключение — пользователь получает alert."""
        from src.bot.handlers.research_cb import cb_research_retry

        cb = _make_callback_query("research:retry:abc123")
        mock_result = _make_research_result("abc123", "test query")

        with (
            patch(
                "src.bot.handlers.research_cb.get_deep_research_pipeline",
            ) as mock_get_pipeline,
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.get_status = AsyncMock(return_value=mock_result)
            mock_pipeline.submit = MagicMock(side_effect=ValueError("Pipeline crashed"))
            mock_get_pipeline.return_value = mock_pipeline

            await cb_research_retry(cb)

            cb.answer.assert_any_call(
                "Ошибка перезапуска: Pipeline crashed", show_alert=True
            )
