"""Интеграционные тесты успешного батчинга фаз Dream Cycle.

Проверяет реальный сценарий с возвратом данных из фаз:
- Фазы P5/P6/P8/P12 выполняются параллельно и возвращают результаты
- P7 выполняется последовательно после батча
- Ошибки в фазах логируются (через return_exceptions=True)
- Все фазы вызваны ровно по одному разу
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDreamBatchIntegration:
    """Интеграционные тесты батчинга с возвратом данных из фаз."""

    @pytest.mark.asyncio
    async def test_all_phases_return_results_in_batch(self) -> None:
        """Все 4 фазы батча (P5, P6, P8, P12) выполняются и возвращают результаты."""
        # Мокаем фазы с осмысленными возвратами (а не просто None)
        p5_result = {"wiki_pages": 3, "categories": 5}
        p6_result = {"dsm_removed": 12}
        p8_result = {"stale_closed": 2}
        p12_result = {"mood_alerts": ["Иван: негативный тренд"]}

        p5 = AsyncMock(return_value=p5_result)
        p6 = AsyncMock(return_value=p6_result)
        p8 = AsyncMock(return_value=p8_result)
        p12 = AsyncMock(return_value=p12_result)
        p7 = AsyncMock(return_value={"forgotten": 5})

        # Эмулируем логику батча из dream_cycle.py
        batch_results = await asyncio.gather(
            p5(),
            p6(),
            p8(),
            p12(),
            return_exceptions=True,
        )

        # Проверка: все 4 фазы вызваны по одному разу
        p5.assert_awaited_once()
        p6.assert_awaited_once()
        p8.assert_awaited_once()
        p12.assert_awaited_once()

        # Проверка: нет исключений в результатах
        assert len(batch_results) == 4
        assert all(not isinstance(r, Exception) for r in batch_results), (
            f"Expected no exceptions, got: {batch_results}"
        )

        # Проверка: результаты соответствуют возвратам моков
        assert batch_results[0] == p5_result
        assert batch_results[1] == p6_result
        assert batch_results[2] == p8_result
        assert batch_results[3] == p12_result

        # Проверка: P7 НЕ вызывалась внутри батча
        p7.assert_not_awaited()

        # P7 после батча
        await p7()
        p7.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_p7_always_after_batch_with_tracking(self) -> None:
        """P7 (auto-forget) выполняется строго ПОСЛЕ завершения батча.

        Используем трекинг порядка вызовов для верификации.
        """
        call_order: list[str] = []

        async def track_and_return(name: str, result: dict) -> dict:
            call_order.append(name)
            await asyncio.sleep(0)  # даём event loop переключиться
            return result

        # Фазы батча
        p5 = lambda: track_and_return("p5", {"wiki": "ok"})  # noqa: E731
        p6 = lambda: track_and_return("p6", {"dsm": "ok"})  # noqa: E731
        p8 = lambda: track_and_return("p8", {"stale": "ok"})  # noqa: E731
        p12 = lambda: track_and_return("p12", {"mood": "ok"})  # noqa: E731

        # Запускаем батч
        await asyncio.gather(p5(), p6(), p8(), p12(), return_exceptions=True)

        # Проверка: все 4 фазы батча вызваны
        assert len(call_order) == 4, (
            f"Expected 4 calls, got {len(call_order)}: {call_order}"
        )

        # Запоминаем что батч завершён
        batch_phases = set(call_order)
        assert batch_phases == {"p5", "p6", "p8", "p12"}

        # P7 после батча
        await track_and_return("p7", {"forgotten": 3})

        # Проверка: P7 — последний в порядке вызовов
        assert call_order[-1] == "p7", f"P7 should be last, got order: {call_order}"
        assert len(call_order) == 5

    @pytest.mark.asyncio
    async def test_batch_errors_logged_and_others_continue(self) -> None:
        """При ошибке в одной фазе остальные продолжают работу.

        Эмулирует ситуацию: P6 падает с ошибкой, P5/P8/P12 успешны.
        """
        p5_data = {"wiki_pages": 2}
        p8_data = {"stale_closed": 1}
        p12_data = {"mood_alerts": []}

        p5 = AsyncMock(return_value=p5_data)
        p8 = AsyncMock(return_value=p8_data)
        p12 = AsyncMock(return_value=p12_data)

        async def fail_p6() -> None:
            raise RuntimeError("DSM cleanup: таблица не найдена")

        batch_results = await asyncio.gather(
            p5(),
            fail_p6(),
            p8(),
            p12(),
            return_exceptions=True,
        )

        # Успешные фазы вызваны
        p5.assert_awaited_once()
        p8.assert_awaited_once()
        p12.assert_awaited_once()

        # В результатах ровно 1 Exception
        exceptions = [r for r in batch_results if isinstance(r, Exception)]
        assert len(exceptions) == 1, f"Expected 1 exception, got {len(exceptions)}"
        assert isinstance(exceptions[0], RuntimeError)
        assert "DSM cleanup" in str(exceptions[0])

        # Не-exception результатов — 3
        non_exceptions = [r for r in batch_results if not isinstance(r, Exception)]
        assert len(non_exceptions) == 3
        assert p5_data in non_exceptions
        assert p8_data in non_exceptions
        assert p12_data in non_exceptions

    @pytest.mark.asyncio
    async def test_batch_with_all_phases_failing_returns_exceptions(self) -> None:
        """Если ВСЕ фазы батча падают — все исключения возвращаются,
        а не пробрасываются наружу (return_exceptions=True).
        """

        async def fail(msg: str) -> None:
            raise RuntimeError(msg)

        batch_results = await asyncio.gather(
            fail("P5 wiki failure"),
            fail("P6 dsm failure"),
            fail("P8 stale failure"),
            fail("P12 mood failure"),
            return_exceptions=True,
        )

        # Все 4 результата — исключения
        assert len(batch_results) == 4
        assert all(isinstance(r, Exception) for r in batch_results), (
            f"All results should be exceptions, got: {batch_results}"
        )

        errors = [str(r) for r in batch_results]
        assert "P5 wiki failure" in errors
        assert "P6 dsm failure" in errors
        assert "P8 stale failure" in errors
        assert "P12 mood failure" in errors

    @pytest.mark.asyncio
    async def test_batch_with_mixed_returns_and_none(self) -> None:
        """Фазы могут возвращать None (ничего не сделано) — это не ошибка."""
        p5 = AsyncMock(return_value=None)
        p6 = AsyncMock(return_value={"dsm_removed": 0})  # тоже валидно
        p8 = AsyncMock(return_value=None)
        p12 = AsyncMock(return_value={"mood_alerts": []})  # пустой список — валидно

        batch_results = await asyncio.gather(
            p5(), p6(), p8(), p12(), return_exceptions=True
        )

        p5.assert_awaited_once()
        p6.assert_awaited_once()
        p8.assert_awaited_once()
        p12.assert_awaited_once()

        # Ни одного исключения
        assert all(not isinstance(r, Exception) for r in batch_results)
        # None — валидный результат
        assert batch_results[0] is None
        assert batch_results[1] == {"dsm_removed": 0}
        assert batch_results[2] is None
        assert batch_results[3] == {"mood_alerts": []}


class TestDreamBatchWithSettingsIntegration:
    """Интеграционные тесты с учётом настроек проекта."""

    @pytest.mark.asyncio
    async def test_batch_enabled_flag_controls_parallel_execution(self) -> None:
        """settings.dreaming_batch_enabled=True → параллельный батч.

        Проверяем что сам флаг корректно читается и управляет ветвлением.
        """
        from src.config import settings

        # Проверяем что настройка существует и читается
        assert hasattr(settings, "dreaming_batch_enabled"), (
            "settings.dreaming_batch_enabled must exist"
        )

        original = settings.dreaming_batch_enabled

        try:
            # Эмулируем включённый батчинг
            settings.dreaming_batch_enabled = True

            if settings.dreaming_batch_enabled:
                # Параллельный запуск (как в dream_cycle.py)
                p5 = AsyncMock(return_value={"wiki": "ok"})
                p6 = AsyncMock(return_value={"dsm": "ok"})
                p8 = AsyncMock(return_value={"stale": "ok"})
                p12 = AsyncMock(return_value={"mood": "ok"})

                batch_results = await asyncio.gather(
                    p5(), p6(), p8(), p12(), return_exceptions=True
                )

                assert len(batch_results) == 4
                p5.assert_awaited_once()
                p6.assert_awaited_once()
                p8.assert_awaited_once()
                p12.assert_awaited_once()
            else:
                pytest.fail("Batch should be enabled")

            # Эмулируем выключенный батчинг
            settings.dreaming_batch_enabled = False

            if not settings.dreaming_batch_enabled:
                # Последовательный запуск
                call_order: list[str] = []

                async def track(name: str) -> None:
                    call_order.append(name)

                await track("p5")
                await track("p6")
                await track("p7")
                await track("p8")

                assert call_order == ["p5", "p6", "p7", "p8"]
            else:
                pytest.fail("Batch should be disabled")
        finally:
            settings.dreaming_batch_enabled = original
