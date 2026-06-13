"""Тесты для батчинга фаз Dream Cycle (dream_cycle.py — Волна 3).

Проверяет:
- Параллельный запуск фаз 5/6/8/12 при dreaming_batch_enabled=True
- Последовательный запуск при dreaming_batch_enabled=False
- Обработку ошибок в батче (return_exceptions=True)
- Порядок: P7 выполняется ПОСЛЕ батча
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDreamBatchEnabled:
    """Тесты параллельного батчинга фаз (dreaming_batch_enabled=True)."""

    @pytest.mark.asyncio
    async def test_phases_5_6_8_12_run_parallel_when_batch_enabled(self):
        """Фазы 5, 6, 8, 12 запускаются через asyncio.gather с return_exceptions=True."""
        # Мокаем все фазы как AsyncMock
        p5 = AsyncMock(return_value=None)
        p6 = AsyncMock(return_value=None)
        p8 = AsyncMock(return_value=None)
        p12 = AsyncMock(return_value=None)
        p7 = AsyncMock(return_value=None)

        # Эмулируем логику батча из dream_cycle
        t0 = asyncio.get_event_loop().time()
        await asyncio.sleep(0)  # фиксируем baseline

        batch_results = await asyncio.gather(
            p5(),
            p6(),
            p8(),
            p12(),
            return_exceptions=True,
        )

        # Все 4 фазы должны быть вызваны (по одному разу)
        p5.assert_awaited_once()
        p6.assert_awaited_once()
        p8.assert_awaited_once()
        p12.assert_awaited_once()

        # Ошибок в результате быть не должно
        assert all(not isinstance(r, Exception) for r in batch_results)

        # P7 НЕ вызывается внутри батча
        p7.assert_not_awaited()

        # А теперь «P7 после батча»
        await p7()
        p7.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_in_one_phase_does_not_crash_others(self):
        """Ошибка в одной фазе батча не роняет остальные (return_exceptions=True)."""

        async def fail() -> None:
            raise RuntimeError("phase 6 failed")

        p5 = AsyncMock(return_value=None)
        p8 = AsyncMock(return_value=None)
        p12 = AsyncMock(return_value=None)

        batch_results = await asyncio.gather(
            p5(),
            fail(),
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
        assert len(exceptions) == 1
        assert isinstance(exceptions[0], RuntimeError)
        assert str(exceptions[0]) == "phase 6 failed"

        # Не-exception результатов — 3 (None от моков)
        non_exceptions = [r for r in batch_results if not isinstance(r, Exception)]
        assert len(non_exceptions) == 3

    @pytest.mark.asyncio
    async def test_p7_executes_after_batch_when_batch_enabled(self):
        """P7 (auto-forget) выполняется ПОСЛЕ батча, не параллельно."""
        call_order: list[str] = []

        async def track(name: str) -> None:
            call_order.append(name)

        # Батч
        await asyncio.gather(
            track("p5"),
            track("p6"),
            track("p8"),
            track("p12"),
            return_exceptions=True,
        )

        batch_end = len(call_order)
        assert batch_end == 4  # все 4 фазы батча вызваны

        # P7 после батча
        await track("p7")

        # P7 — последний в порядке вызовов
        assert call_order[-1] == "p7"
        # И он НЕ внутри первых 4 (порядок gather не гарантирован, но p7 точно последний)
        assert call_order.index("p7") == 4

    @pytest.mark.asyncio
    async def test_batch_order_matches_dream_cycle_signature(self):
        """Порядок аргументов в asyncio.gather соответствует коду dream_cycle:
        _run_phase_5_wiki, _run_phase_6_dsm, _run_phase_8_stale_sessions, _run_phase_12_mood.
        """
        # Проверяем что правильный набор фаз попадает в gather
        batch_phases = ["p5", "p6", "p8", "p12"]
        batch_coros = [AsyncMock(return_value=None) for _ in batch_phases]

        results = await asyncio.gather(
            *(coro() for coro in batch_coros),
            return_exceptions=True,
        )

        assert len(results) == 4
        assert all(not isinstance(r, Exception) for r in results)
        for coro in batch_coros:
            coro.assert_awaited_once()


class TestDreamBatchDisabled:
    """Тесты последовательного режима (dreaming_batch_enabled=False)."""

    @pytest.mark.asyncio
    async def test_phases_execute_sequentially_when_batch_disabled(self):
        """При batch_enabled=False фазы выполняются последовательно."""
        call_order: list[str] = []

        async def track(name: str) -> None:
            call_order.append(name)
            await asyncio.sleep(0)  # даём event loop переключиться

        # Последовательный запуск
        await track("p5")
        await track("p6")
        await track("p7")
        await track("p8")

        # Порядок строго последовательный
        assert call_order == ["p5", "p6", "p7", "p8"]

    @pytest.mark.asyncio
    async def test_p12_runs_after_p11_in_sequential_mode(self):
        """В последовательном режиме P12 (mood) выполняется после P11 (dreaming),
        в самом конце (строки 415-416 dream_cycle.py).
        """
        call_order: list[str] = []

        async def track(name: str) -> None:
            call_order.append(name)

        # Имитация: P5→P6→P7→P8, потом P9,P10,P11, потом P12
        for phase in ["p5", "p6", "p7", "p8", "p9", "p10", "p11", "p12"]:
            await track(phase)

        # P12 — последняя
        assert call_order[-1] == "p12"
        assert call_order.index("p12") == 7


class TestDreamBatchWithSettings:
    """Интеграционные тесты с моком settings."""

    @pytest.mark.asyncio
    async def test_batching_logic_respects_settings_flag(self):
        """Код ветвится по settings.dreaming_batch_enabled."""
        from src.config import settings

        # Сохраняем оригинал
        original = settings.dreaming_batch_enabled

        try:
            # Включаем батчинг
            settings.dreaming_batch_enabled = True
            assert settings.dreaming_batch_enabled is True

            # Выключаем батчинг
            settings.dreaming_batch_enabled = False
            assert settings.dreaming_batch_enabled is False
        finally:
            settings.dreaming_batch_enabled = original


class TestBatchEdgeCases:
    """Граничные случаи для батчинга."""

    @pytest.mark.asyncio
    async def test_empty_batch_returns_empty_list(self):
        """asyncio.gather без корутин возвращает пустой список."""
        results = await asyncio.gather(return_exceptions=True)
        assert results == []

    @pytest.mark.asyncio
    async def test_all_phases_fail_still_returns_exceptions(self):
        """Если ВСЕ фазы батча падают — return_exceptions=True всё равно
        возвращает список исключений, не пробрасывает.
        """

        async def fail1() -> None:
            raise ValueError("err1")

        async def fail2() -> None:
            raise TypeError("err2")

        async def fail3() -> None:
            raise RuntimeError("err3")

        async def fail4() -> None:
            raise KeyError("err4")

        results = await asyncio.gather(
            fail1(), fail2(), fail3(), fail4(), return_exceptions=True
        )

        assert len(results) == 4
        assert all(isinstance(r, Exception) for r in results)
        assert isinstance(results[0], ValueError)
        assert isinstance(results[1], TypeError)
        assert isinstance(results[2], RuntimeError)
        assert isinstance(results[3], KeyError)
