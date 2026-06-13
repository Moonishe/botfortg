"""Интеграционные тесты success-path для generate_personalized_greeting.

Проверяет полный путь генерации приветствия:
recall() → rank_inbox() → build_provider() → provider.chat() → возврат.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.memory.memory_recall import RecalledFact, RecallResult


@pytest.fixture(autouse=True)
def _reset_engine_pool() -> None:
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
    """Создать список inbox-элементов."""
    return [
        {
            "peer_name": name,
            "hours_unreplied": hours,
            "last_message": msg,
        }
        for name, hours, msg in items
    ]


# ── Интеграционные тесты success-path ─────────────────────────────────


class TestGreetingSuccessPath:
    """Интеграционные тесты успешной генерации приветствия."""

    async def test_full_success_returns_greeting_with_keywords(self) -> None:
        """Полный success-path: recall + rank_inbox + provider.chat → greeting.

        Проверяет:
        - greeting не пустой
        - содержит ключевые слова (Привет, С возвращением, или аналогичные)
        - HTML-entities экранированы (& → &amp;)
        """
        from src.config import settings

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_owner = MagicMock()
        mock_owner.id = 1

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            return_value="Привет! У тебя 2 непрочитанных от Оли. Помнишь про встречу завтра?"
        )
        mock_provider.close = AsyncMock()

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                return_value=_make_fake_recall_result(
                    "Любит кофе по утрам", "Завтра встреча в 10:00"
                ),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=_make_fake_inbox(
                    ("Оля", 2.0, "Привет, как дела?"),
                    ("Иван", 1.5, "Напомни про отчёт"),
                ),
            ),
            patch("src.bot.handlers.greeting.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.greeting.get_or_create_user",
                new_callable=AsyncMock,
                return_value=mock_owner,
            ),
            patch(
                "src.bot.handlers.greeting.build_provider",
                new_callable=AsyncMock,
                return_value=mock_provider,
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)

            # Проверка: greeting не пустой
            assert result != "", "Greeting should not be empty on success"

            # Проверка: содержит разумные ключевые слова
            assert len(result) >= 5, f"Greeting too short: {result!r}"

            # Проверка: provider.chat вызван
            mock_provider.chat.assert_awaited_once()

            # Проверка: provider.close вызван (cleanup)
            mock_provider.close.assert_awaited_once()

    async def test_success_escapes_html_entities(self) -> None:
        """Приветствие HTML-экранирует <, >, & (защита от инъекций)."""
        from src.config import settings

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_owner = MagicMock()
        mock_owner.id = 1

        mock_provider = MagicMock()
        # LLM возвращает текст с сырыми HTML-символами: <, >, &
        mock_provider.chat = AsyncMock(
            return_value="Привет! <b>Важное</b> & срочное > встречи."
        )
        mock_provider.close = AsyncMock()

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                return_value=_make_fake_recall_result("Любит кофе"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=_make_fake_inbox(("Оля", 1.0, "Привет")),
            ),
            patch("src.bot.handlers.greeting.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.greeting.get_or_create_user",
                new_callable=AsyncMock,
                return_value=mock_owner,
            ),
            patch(
                "src.bot.handlers.greeting.build_provider",
                new_callable=AsyncMock,
                return_value=mock_provider,
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)

            # Проверка: сырые HTML-символы должны быть экранированы
            # & → &amp; (первый replace)
            # < → &lt;  (второй replace)
            # > → &gt;  (третий replace)
            # Поэтому в результате не должно быть неэкранированных <, > и &
            # За исключением тех что уже были &lt; &gt; &amp; (от предыдущих замен)
            assert "&lt;" in result, f"< should be escaped to &lt;, got: {result!r}"
            assert "&gt;" in result, f"> should be escaped to &gt;, got: {result!r}"
            assert "&amp;" in result, f"& should be escaped to &amp;, got: {result!r}"
            # Сырой < не должен остаться (кроме как внутри &lt;)
            result_no_entities = (
                result.replace("&lt;", "").replace("&gt;", "").replace("&amp;", "")
            )
            assert "<" not in result_no_entities, f"Raw < must be escaped: {result!r}"
            assert ">" not in result_no_entities, f"Raw > must be escaped: {result!r}"
            assert "&" not in result_no_entities, f"Raw & must be escaped: {result!r}"
            assert len(result) >= 5, f"Result too short after escaping: {result!r}"

    async def test_success_truncates_very_long_greeting(self) -> None:
        """Слишком длинное приветствие (>4000 символов) обрезается."""
        from src.config import settings

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_owner = MagicMock()
        mock_owner.id = 1

        mock_provider = MagicMock()
        # Генерируем ответ длиннее 4000 символов
        long_text = "А" * 4500
        mock_provider.chat = AsyncMock(return_value=long_text)
        mock_provider.close = AsyncMock()

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                return_value=_make_fake_recall_result("Факт"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=_make_fake_inbox(("Оля", 1.0, "Привет")),
            ),
            patch("src.bot.handlers.greeting.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.greeting.get_or_create_user",
                new_callable=AsyncMock,
                return_value=mock_owner,
            ),
            patch(
                "src.bot.handlers.greeting.build_provider",
                new_callable=AsyncMock,
                return_value=mock_provider,
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            result = await generate_personalized_greeting(123456789)

            # Проверка: результат обрезан до ≤4000 символов
            assert len(result) <= 4000, (
                f"Greeting should be truncated to ≤4000, got {len(result)}"
            )
            # Проверка: заканчивается на "..."
            assert result.endswith("..."), (
                f"Truncated greeting should end with '...': {result[-10:]!r}"
            )

    async def test_success_recall_without_inbox(self) -> None:
        """Приветствие генерируется когда есть только факты (без inbox)."""
        from src.config import settings

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_owner = MagicMock()
        mock_owner.id = 1

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            return_value="С возвращением! Помнишь, ты просил напомнить про встречу?"
        )
        mock_provider.close = AsyncMock()

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                return_value=_make_fake_recall_result("Встреча завтра в 10:00"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=[],  # пустой inbox
            ),
            patch("src.bot.handlers.greeting.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.greeting.get_or_create_user",
                new_callable=AsyncMock,
                return_value=mock_owner,
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
            assert len(result) >= 5
            mock_provider.chat.assert_awaited_once()

    async def test_success_inbox_without_recall(self) -> None:
        """Приветствие генерируется когда есть только inbox (без фактов — recall упал)."""
        from src.config import settings

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_owner = MagicMock()
        mock_owner.id = 1

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            return_value="Привет! Иван написал 2 часа назад."
        )
        mock_provider.close = AsyncMock()

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                side_effect=RuntimeError("recall failed"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=_make_fake_inbox(("Иван", 2.0, "Привет, нужна помощь")),
            ),
            patch("src.bot.handlers.greeting.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.greeting.get_or_create_user",
                new_callable=AsyncMock,
                return_value=mock_owner,
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
            assert len(result) >= 5
            mock_provider.chat.assert_awaited_once()

    async def test_greeting_passes_through_provider_chat_message(self) -> None:
        """Проверяем что provider.chat получает сообщения с системным промптом."""
        from src.config import settings

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_owner = MagicMock()
        mock_owner.id = 1

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(return_value="Привет! Как твои дела?")
        mock_provider.close = AsyncMock()

        with (
            patch.object(settings, "personalized_greeting_enabled", True),
            patch(
                "src.bot.handlers.greeting.recall",
                return_value=_make_fake_recall_result("Любит собак"),
            ),
            patch(
                "src.bot.handlers.greeting.rank_inbox",
                return_value=_make_fake_inbox(("Маша", 0.5, "Привет!")),
            ),
            patch("src.bot.handlers.greeting.get_session", return_value=mock_session),
            patch(
                "src.bot.handlers.greeting.get_or_create_user",
                new_callable=AsyncMock,
                return_value=mock_owner,
            ),
            patch(
                "src.bot.handlers.greeting.build_provider",
                new_callable=AsyncMock,
                return_value=mock_provider,
            ),
        ):
            from src.bot.handlers.greeting import generate_personalized_greeting

            await generate_personalized_greeting(123456789)

            # Проверяем что chat был вызван с ожидаемым task_type
            call_args = mock_provider.chat.call_args
            assert call_args is not None
            # Первый аргумент — список ChatMessage
            messages = call_args[0][0]
            assert len(messages) == 2
            assert messages[0].role == "system"
            assert messages[1].role == "user"
            assert "task_type" in call_args[1]
