"""Тесты для generate_personalized_greeting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.memory.memory_recall import RecalledFact, RecallResult


@pytest.fixture(autouse=True)
def _reset_engine_pool():
    """Dispose stale connections before each test (from conftest)."""
    try:
        from src.db.session import engine

        engine.sync_engine.dispose()
    except Exception:
        pass


# ── Вспомогательные фабрики ──────────────────────────────────────────


def _make_fake_recall_result(*facts: str) -> RecallResult:
    """Создать RecallResult с указанными фактами."""
    return RecallResult(
        facts=[RecalledFact(fact=f, reason="📌 закреплён") for f in facts],
        meta={},
    )


def _make_fake_inbox(*items: tuple[str, float, str]) -> list[dict]:
    """Создать список inbox-элементов.

    Каждый элемент: (peer_name, hours_unreplied, last_message).
    """
    return [
        {
            "peer_name": name,
            "hours_unreplied": hours,
            "last_message": msg,
        }
        for name, hours, msg in items
    ]


# ── Тесты ─────────────────────────────────────────────────────────────


class TestFeatureGate:
    """Тесты feature-gate: персонализированное приветствие выключено."""

    async def test_disabled_returns_empty(self) -> None:
        """Если personalized_greeting_enabled=False — возвращаем ''."""
        from src.config import settings

        with patch.object(settings, "personalized_greeting_enabled", False):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)
            assert result == ""


class TestErrorsReturnEmpty:
    """Тесты что при ошибке возвращается fallback-приветствие (fail-safe v2)."""

    async def test_recall_error_returns_fallback(self) -> None:
        """Если recall() бросает исключение, но есть inbox — fallback-приветствие."""
        from src.config import settings

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                side_effect=RuntimeError("DB exploded"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=_make_fake_inbox(("Иван", 2.0, "Привет!")),
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)
            assert result != ""
            assert "непрочитанных" in result

    async def test_inbox_error_returns_fallback(self) -> None:
        """Если rank_inbox() бросает исключение, но есть факты — fallback-приветствие."""
        from src.config import settings

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                return_value=_make_fake_recall_result("Люблю кофе"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                side_effect=ConnectionError("Telegram API down"),
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)
            assert result != ""
            assert "Люблю кофе" in result

    async def test_build_provider_returns_none(self) -> None:
        """Если build_provider() возвращает None — fallback-приветствие."""
        from src.config import settings

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                return_value=_make_fake_recall_result("Люблю кофе"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=_make_fake_inbox(("Иван", 2.0, "Привет!")),
            ),
            patch("src.bot.handlers.greeting.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.greeting.get_or_create_user",
                new_callable=AsyncMock,
            ),
            patch(
                "src.bot.handlers.greeting.build_provider",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)
            assert result != ""
            assert "Люблю кофе" in result
            assert "непрочитанных" in result


class TestNoDataReturnsEmpty:
    """Тесты что при отсутствии фактов и непрочитанных возвращается ''."""

    async def test_no_facts_no_inbox_returns_empty(self) -> None:
        """Если recall пуст и inbox пуст — возвращаем ''."""
        from src.config import settings

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                return_value=_make_fake_recall_result(),  # пусто
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=[],  # пусто
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)
            assert result == ""

    async def test_recall_none_no_inbox_returns_empty(self) -> None:
        """Если recall упал (failsafe) и inbox пуст — возвращаем ''."""
        from src.config import settings

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                side_effect=RuntimeError("fail"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=[],
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)
            assert result == ""


class TestFormatFunctions:
    """Тесты хелперов форматирования."""

    def test_format_facts_empty(self) -> None:
        """Пустой recall_result — «Нет релевантных фактов.»."""
        from src.bot.handlers.greeting import _format_facts

        result = _format_facts(_make_fake_recall_result())
        assert "Нет релевантных фактов" in result

    def test_format_facts_with_items(self) -> None:
        """Факты форматируются с префиксом «-»."""
        from src.bot.handlers.greeting import _format_facts

        rr = _make_fake_recall_result("Факт 1", "Факт 2")
        result = _format_facts(rr)
        assert "- Факт 1" in result
        assert "- Факт 2" in result

    def test_format_inbox_empty(self) -> None:
        """Пустой inbox — «Нет непрочитанных сообщений.»."""
        from src.bot.handlers.greeting import _format_inbox

        result = _format_inbox([])
        assert "Нет непрочитанных сообщений" in result

    def test_format_inbox_with_items(self) -> None:
        """Inbox-элементы форматируются с именем и временем."""
        from src.bot.handlers.greeting import _format_inbox

        inbox = _make_fake_inbox(
            ("Иван", 2.5, "Привет, как дела?"),
            ("Оля", 1.0, ""),
        )
        result = _format_inbox(inbox)
        assert "Иван" in result
        assert "2ч" in result
        assert "«Привет, как дела?»" in result
        assert "Оля" in result
        assert "1ч" in result

    def test_format_facts_limit_five(self) -> None:
        """Форматтер ограничивает вывод 5 фактами."""
        from src.bot.handlers.greeting import _format_facts

        rr = _make_fake_recall_result(*[f"Факт {i}" for i in range(10)])
        result = _format_facts(rr)
        # Должно быть ровно 5 строк фактов
        fact_lines = [l for l in result.split("\n") if l.startswith("- ")]
        assert len(fact_lines) == 5

    def test_format_inbox_limit_three(self) -> None:
        """Форматтер ограничивает вывод 3 inbox-элементами."""
        from src.bot.handlers.greeting import _format_inbox

        inbox = _make_fake_inbox(
            *[(f"Контакт{i}", 1.0 * i, f"Сообщение {i}") for i in range(1, 10)]
        )
        result = _format_inbox(inbox)
        fact_lines = [l for l in result.split("\n") if l.startswith("- ")]
        assert len(fact_lines) == 3


class TestLLMErrors:
    """Тесты ошибок на этапе LLM-вызова — теперь возвращается fallback."""

    async def test_llm_chat_exception_returns_fallback(self) -> None:
        """Если provider.chat() бросает исключение — fallback-приветствие."""
        from src.config import settings

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        mock_provider.close = AsyncMock()

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                return_value=_make_fake_recall_result("Люблю кофе"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=_make_fake_inbox(("Иван", 2.0, "Привет!")),
            ),
            patch("src.bot.handlers.greeting.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.greeting.get_or_create_user",
                new_callable=AsyncMock,
            ),
            patch(
                "src.bot.handlers.greeting.build_provider",
                new_callable=AsyncMock,
                return_value=mock_provider,
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)
            assert result != ""
            assert "Люблю кофе" in result

    async def test_llm_too_short_returns_fallback(self) -> None:
        """Если LLM вернул меньше 5 символов — fallback-приветствие."""
        from src.config import settings

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(return_value="ok")
        mock_provider.close = AsyncMock()

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                return_value=_make_fake_recall_result("Люблю кофе"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=_make_fake_inbox(("Иван", 2.0, "Привет!")),
            ),
            patch("src.bot.handlers.greeting.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.greeting.get_or_create_user",
                new_callable=AsyncMock,
            ),
            patch(
                "src.bot.handlers.greeting.build_provider",
                new_callable=AsyncMock,
                return_value=mock_provider,
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)
            assert result != ""
            assert "Люблю кофе" in result
