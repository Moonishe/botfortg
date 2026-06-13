"""Тесты для callback-хендлеров Deep Research.

Актуализированы под реальный API:
- _parse_research_cb возвращает (action, job_id) — 2-tuple без HMAC.
- В модуле нет _verify_callback / feature-gate проверок.
- Callback data: research:<action>:<job_id>.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_engine_pool() -> None:
    """Dispose stale connections before each test (from conftest)."""
    try:
        from src.db.session import engine

        engine.sync_engine.dispose()
    except Exception:
        pass


# ── Вспомогательные фабрики ──────────────────────────────────────────


def _make_callback_query(
    data: str,
    from_user_id: int = 123456789,
    message: MagicMock | None = None,
) -> MagicMock:
    """Создать мок CallbackQuery с указанными callback_data.

    Используем MagicMock без spec чтобы избежать конфликтов
    с aiogram-типами (InaccessibleMessage и т.д.).
    """
    if message is None:
        message = MagicMock()
        message.delete = AsyncMock()
        message.edit_text = AsyncMock()
        message.answer_document = AsyncMock()

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
    summary: str = "Test summary",
) -> MagicMock:
    """Создать мок ResearchResult."""
    from src.core.rag.types import ResearchStatus

    result = MagicMock()
    result.job_id = job_id
    result.query = query
    result.status = ResearchStatus(status)
    result.summary = summary
    return result


# ── _parse_research_cb тесты ──────────────────────────────────────────


class TestParseResearchCb:
    """Тесты парсинга callback_data: research:<action>:<job_id>."""

    def test_valid_callback_data(self) -> None:
        """Валидная callback_data разбирается корректно."""
        from src.bot.handlers.research_cb import _parse_research_cb

        result = _parse_research_cb("research:view:abc123")
        assert result is not None
        action, job_id = result
        assert action == "view"
        assert job_id == "abc123"

    def test_invalid_prefix_returns_none(self) -> None:
        """Неверный префикс — возвращаем None."""
        from src.bot.handlers.research_cb import _parse_research_cb

        result = _parse_research_cb("wrong:view:abc123")
        assert result is None

    def test_too_few_parts_returns_none(self) -> None:
        """Меньше 3 частей — возвращаем None."""
        from src.bot.handlers.research_cb import _parse_research_cb

        result = _parse_research_cb("research:view")
        assert result is None

    def test_extra_parts_kept_in_job_id(self) -> None:
        """Больше 3 частей — остаток попадает в job_id (split maxsplit=2)."""
        from src.bot.handlers.research_cb import _parse_research_cb

        result = _parse_research_cb("research:view:abc123:extra:parts")
        assert result is not None
        action, job_id = result
        assert action == "view"
        assert job_id == "abc123:extra:parts"

    def test_empty_data_returns_none(self) -> None:
        """Пустая callback_data — возвращаем None."""
        from src.bot.handlers.research_cb import _parse_research_cb

        result = _parse_research_cb("")
        assert result is None

    def test_callback_data_with_colons_in_job_id(self) -> None:
        """Двоеточия в job_id — попадают в job_id благодаря maxsplit=2."""
        from src.bot.handlers.research_cb import _parse_research_cb

        result = _parse_research_cb("research:delete:job:with:colon")
        assert result is not None
        action, job_id = result
        assert action == "delete"
        assert job_id == "job:with:colon"


# ── cb_research_view тесты ────────────────────────────────────────────


class TestCbResearchView:
    """Тесты хендлера cb_research_view."""

    @pytest.mark.asyncio
    async def test_nonexistent_job_id(self) -> None:
        """Несуществующий job_id → callback.answer с предупреждением."""
        from src.bot.handlers.research_cb import cb_research_view

        cb = _make_callback_query("research:view:noexist")

        with patch(
            "src.bot.handlers.research_cb.get_deep_research_pipeline",
        ) as mock_get_pipeline:
            mock_pipeline = MagicMock()
            mock_pipeline.get_status = AsyncMock(return_value=None)
            mock_get_pipeline.return_value = mock_pipeline

            await cb_research_view(cb)

            cb.answer.assert_called_with(
                "Задача не найдена или устарела.", show_alert=True
            )

    @pytest.mark.asyncio
    async def test_invalid_callback_data(self) -> None:
        """Невалидный формат → ответ об ошибке данных."""
        from src.bot.handlers.research_cb import cb_research_view

        cb = _make_callback_query("wrong:data")

        await cb_research_view(cb)

        cb.answer.assert_called_with("Ошибка данных.", show_alert=True)

    @pytest.mark.asyncio
    async def test_message_not_accessible(self) -> None:
        """callback.message не Message → ответ-предупреждение."""
        from src.bot.handlers.research_cb import cb_research_view

        cb = _make_callback_query("research:view:abc123")

        with patch(
            "src.bot.handlers.research_cb.get_deep_research_pipeline",
        ) as mock_get_pipeline:
            mock_pipeline = MagicMock()
            mock_pipeline.get_status = AsyncMock(
                return_value=_make_research_result("abc123", "test query")
            )
            mock_get_pipeline.return_value = mock_pipeline

            await cb_research_view(cb)

            cb.answer.assert_called_with(
                "Сообщение недоступно для ответа.", show_alert=True
            )


# ── cb_research_delete тесты ──────────────────────────────────────────


class TestCbResearchDelete:
    """Тесты хендлера cb_research_delete.

    Проверяем guard-логику: msg is None / не-Message / невалидные данные.
    """

    @pytest.mark.asyncio
    async def test_delete_invalid_data(self) -> None:
        """Невалидный формат callback_data → ответ об ошибке."""
        from src.bot.handlers.research_cb import cb_research_delete

        cb = _make_callback_query("wrong:data")

        await cb_research_delete(cb)

        cb.answer.assert_called_with("Ошибка данных.", show_alert=True)

    @pytest.mark.asyncio
    async def test_delete_no_message(self) -> None:
        """Нет сообщения для удаления → ответ-предупреждение."""
        from src.bot.handlers.research_cb import cb_research_delete

        cb = _make_callback_query("research:delete:abc123")
        cb.message = None

        await cb_research_delete(cb)

        cb.answer.assert_called_with("Сообщение не найдено.", show_alert=True)

    @pytest.mark.asyncio
    async def test_delete_non_message_type(self) -> None:
        """MagicMock не проходит isinstance(msg, Message) → предупреждение."""
        from src.bot.handlers.research_cb import cb_research_delete

        cb = _make_callback_query("research:delete:abc123")

        await cb_research_delete(cb)

        cb.answer.assert_called_with("Не могу удалить это сообщение.", show_alert=True)


# ── cb_research_retry тесты ───────────────────────────────────────────


class TestCbResearchRetry:
    """Тесты хендлера cb_research_retry."""

    @pytest.mark.asyncio
    async def test_retry_nonexistent_job_id(self) -> None:
        """Несуществующий job_id → ответ-предупреждение."""
        from src.bot.handlers.research_cb import cb_research_retry

        cb = _make_callback_query("research:retry:noexist")

        with patch(
            "src.bot.handlers.research_cb.get_deep_research_pipeline",
        ) as mock_get_pipeline:
            mock_pipeline = MagicMock()
            mock_pipeline.get_status = AsyncMock(return_value=None)
            mock_get_pipeline.return_value = mock_pipeline

            await cb_research_retry(cb)

            cb.answer.assert_called_with(
                "Задача не найдена или устарела.", show_alert=True
            )

    @pytest.mark.asyncio
    async def test_retry_invalid_data(self) -> None:
        """Невалидный формат callback_data → ответ об ошибке."""
        from src.bot.handlers.research_cb import cb_research_retry

        cb = _make_callback_query("wrong:data")

        await cb_research_retry(cb)

        cb.answer.assert_called_with("Ошибка данных.", show_alert=True)
