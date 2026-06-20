"""Тесты для оптимистичного prefetch recall (S1-T1).

Проверяют:
  - cache hit (свежий результат)
  - cache miss (протухший / отсутствует)
  - конкурентный доступ под asyncio.Lock
  - feature-флаг prefetch_recall_enabled
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import MagicMock, patch

import pytest

# ⚠️ Устанавливаем переменные окружения ДО импорта src-модулей —
#    иначе pydantic Settings упадёт на валидации bot_token.
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
# Валидный тестовый токен: 12 цифр : 35 символов (входит в диапазон 30-50)
os.environ["BOT_TOKEN"] = "123456789012:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789"
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")
from src.config import settings
from src.core.memory.memory_recall import RecallResult, RecalledFact


# ── Helpers ────────────────────────────────────────────────────────────────

_MOCK_RECALL_PATH = "src.core.memory.memory_recall.recall"
_MOCK_FORMAT_PATH = "src.core.memory.memory_recall.format_recall_for_prompt"

# Pre-import модуля чтобы гарантировать что memory_recall загружен до патчей
import src.core.memory.prefetch_recall as _pf_mod  # noqa: E402


def _fake_recall_result(facts: list[str] | None = None) -> RecallResult:
    """Фабрика RecallResult с заданными фактами."""
    if facts is None:
        facts = ["тестовый факт 1", "тестовый факт 2"]
    result = RecallResult()
    for i, f in enumerate(facts):
        result.facts.append(
            RecalledFact(
                fact=f,
                reason="тест",
                confidence=0.9,
                memory_id=100 + i,
            )
        )
    return result


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _enable_prefetch():
    """Включаем prefetch перед каждым тестом."""
    original = settings.prefetch_recall_enabled
    settings.prefetch_recall_enabled = True
    yield
    settings.prefetch_recall_enabled = original


@pytest.fixture(autouse=True)
def _clear_cache():
    """Очищаем prefetch-кэш между тестами."""
    from src.core.memory.prefetch_recall import _prefetch_cache

    _prefetch_cache.clear()
    yield
    _prefetch_cache.clear()


def _patch_recall(return_value=None, side_effect=None):
    """Удобный враппер для патча recall + format_recall_for_prompt."""
    fake_result = return_value or _fake_recall_result()
    formatted = _format_fake(fake_result)
    return patch.multiple(
        "src.core.memory.memory_recall",
        recall=MagicMock(return_value=fake_result, side_effect=side_effect),
        format_recall_for_prompt=MagicMock(return_value=formatted),
    )


def _format_fake(result: RecallResult) -> str:
    """Форматирует RecallResult в строку, похожую на format_recall_for_prompt."""
    if not result.facts:
        return ""
    lines = ["<recall_context>"]
    for rf in result.facts:
        lines.append(f"[{rf.reason}] {rf.fact}")
    lines.append("</recall_context>")
    return "\n".join(lines)


# ── Cache hit (fresh) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_hit_fresh():
    """После prefetch_recall — get_prefetched_recall возвращает свежий результат."""
    fake_result = _fake_recall_result(["факт A", "факт B"])

    with (
        patch(_MOCK_RECALL_PATH, return_value=fake_result) as mock_recall,
        patch(_MOCK_FORMAT_PATH, return_value=_format_fake(fake_result)),
    ):
        from src.core.memory.prefetch_recall import (
            prefetch_recall,
            get_prefetched_recall,
        )

        # Выполняем prefetch (не fire-and-forget — ждём для теста)
        data = await prefetch_recall(12345, "тестовый запрос")
        assert data is not None, "prefetch_recall должен вернуть dict"
        assert data["facts_count"] == 2
        assert "факт A" in data["memory_context"]
        assert data["mode"] == "light"

        mock_recall.assert_called_once()

        # Сразу после prefetch — get должен вернуть тот же результат
        cached = await get_prefetched_recall(12345)
        assert cached is not None, "get_prefetched_recall должен найти кэш"
        assert cached["facts_count"] == 2
        assert cached["memory_context"] == data["memory_context"]


# ── Cache miss (absent) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_miss_no_prefetch():
    """Без предварительного prefetch — get_prefetched_recall возвращает None."""
    from src.core.memory.prefetch_recall import get_prefetched_recall

    result = await get_prefetched_recall(99999)
    assert result is None, "Без prefetch должен быть None"


# ── Cache miss (expired / stale) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_miss_expired():
    """После истечения TTL — get_prefetched_recall возвращает None и чистит кэш."""
    fake_result = _fake_recall_result(["факт X"])

    with (
        patch(_MOCK_RECALL_PATH, return_value=fake_result),
        patch(_MOCK_FORMAT_PATH, return_value=_format_fake(fake_result)),
    ):
        from src.core.memory.prefetch_recall import (
            prefetch_recall,
            get_prefetched_recall,
            _prefetch_cache,
        )

        # TTL по умолчанию 5 секунд
        await prefetch_recall(11111, "запрос")

        # Проверяем что запись в кэше есть
        assert 11111 in _prefetch_cache

        # Симулируем истечение TTL: подменяем expiry на прошлое
        from src.core.memory.prefetch_recall import _prefetch_lock

        async with _prefetch_lock:
            entry = _prefetch_cache.get(11111)
            if entry:
                # Подменяем expiry на 100 секунд назад
                old_data = entry[1]
                _prefetch_cache[11111] = (time.monotonic() - 100.0, old_data)

        # Теперь get должен вернуть None (протухло)
        cached = await get_prefetched_recall(11111)
        assert cached is None, "Протухший кэш должен вернуть None"

        # Запись должна быть удалена из кэша
        async with _prefetch_lock:
            assert 11111 not in _prefetch_cache, "Протухшая запись должна быть удалена"


# ── Concurrent access under lock ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_prefetch_same_user():
    """Параллельные prefetch для одного user_id не ломают кэш."""
    fake_result = _fake_recall_result(["конкурентный факт"])

    with (
        patch(_MOCK_RECALL_PATH, return_value=fake_result),
        patch(_MOCK_FORMAT_PATH, return_value=_format_fake(fake_result)),
    ):
        from src.core.memory.prefetch_recall import (
            prefetch_recall,
            get_prefetched_recall,
        )

        # Запускаем 5 параллельных prefetch для одного пользователя
        tasks = [
            asyncio.create_task(prefetch_recall(22222, f"запрос {i}")) for i in range(5)
        ]
        results = await asyncio.gather(*tasks)

        # Все должны завершиться без ошибок
        for i, r in enumerate(results):
            assert r is not None, f"prefetch {i} вернул None"
            assert r["facts_count"] == 1

        # Конечный результат — последний записавший побеждает (нормально)
        cached = await get_prefetched_recall(22222)
        assert cached is not None
        assert cached["facts_count"] == 1


@pytest.mark.asyncio
async def test_concurrent_read_write():
    """Конкурентные чтения и записи не вызывают гонок."""
    fake_result = _fake_recall_result(["rw факт"])

    with (
        patch(_MOCK_RECALL_PATH, return_value=fake_result),
        patch(_MOCK_FORMAT_PATH, return_value=_format_fake(fake_result)),
    ):
        from src.core.memory.prefetch_recall import (
            prefetch_recall,
            get_prefetched_recall,
        )

        async def reader(uid: int, n: int):
            """Читатель: многократно вызывает get_prefetched_recall."""
            for _ in range(n):
                await get_prefetched_recall(uid)
                await asyncio.sleep(0)  # yield event loop

        async def writer(uid: int, n: int):
            """Писатель: многократно вызывает prefetch_recall."""
            for i in range(n):
                await prefetch_recall(uid, f"запрос {i}")
                await asyncio.sleep(0)

        # Запускаем 3 читателя + 2 писателя параллельно
        readers = [asyncio.create_task(reader(33333, 10)) for _ in range(3)]
        writers = [asyncio.create_task(writer(33333, 5)) for _ in range(2)]

        await asyncio.gather(*readers, *writers)

        # Проверяем что кэш не повреждён (нет исключений = успех)
        cached = await get_prefetched_recall(33333)
        # Может быть None если последняя запись протухла, но не должно быть исключений
        if cached is not None:
            assert "rw факт" in cached["memory_context"]


# ── Feature flag disabled ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prefetch_disabled_flag():
    """При prefetch_recall_enabled=False — prefetch и get возвращают None."""
    settings.prefetch_recall_enabled = False

    fake_result = _fake_recall_result(["не должен использоваться"])

    with (
        patch(_MOCK_RECALL_PATH, return_value=fake_result) as mock_recall,
        patch(_MOCK_FORMAT_PATH, return_value=_format_fake(fake_result)),
    ):
        from src.core.memory.prefetch_recall import (
            prefetch_recall,
            get_prefetched_recall,
        )

        # prefetch не должен вызывать recall
        data = await prefetch_recall(44444, "запрос")
        assert data is None, "При выключенном флаге prefetch возвращает None"

        # get тоже возвращает None
        cached = await get_prefetched_recall(44444)
        assert cached is None, "При выключенном флаге get возвращает None"

        # recall НЕ должен быть вызван
        mock_recall.assert_not_called()


@pytest.mark.asyncio
async def test_prefetch_disabled_then_enabled():
    """Включение флага после отключения восстанавливает prefetch."""
    settings.prefetch_recall_enabled = False

    from src.core.memory.prefetch_recall import (
        prefetch_recall,
        get_prefetched_recall,
    )

    # С выключенным флагом
    assert await get_prefetched_recall(55555) is None

    # Включаем
    settings.prefetch_recall_enabled = True

    fake_result = _fake_recall_result(["восстановлен"])

    with (
        patch(_MOCK_RECALL_PATH, return_value=fake_result),
        patch(_MOCK_FORMAT_PATH, return_value=_format_fake(fake_result)),
    ):
        data = await prefetch_recall(55555, "запрос")
        assert data is not None
        assert data["facts_count"] == 1

        cached = await get_prefetched_recall(55555)
        assert cached is not None
        assert "восстановлен" in cached["memory_context"]


# ── Edge cases ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prefetch_with_exception_returns_none():
    """Если recall падает — prefetch_recall возвращает None, не роняя вызывающий код."""
    with patch(
        _MOCK_RECALL_PATH,
        side_effect=RuntimeError("DB unavailable"),
    ):
        from src.core.memory.prefetch_recall import prefetch_recall

        # Не должно быть исключения
        data = await prefetch_recall(66666, "запрос при ошибке")
        assert data is None, "При ошибке recall — возвращаем None"


@pytest.mark.asyncio
async def test_multiple_users_independent_caches():
    """Кэши разных пользователей независимы."""
    fake_a = _fake_recall_result(["факт пользователя A"])
    fake_b = _fake_recall_result(["факт пользователя B"])

    # Создаём side_effect чтобы возвращать разные результаты для разных user_id
    async def _fake_recall(*args, **kwargs):
        uid = args[0] if args else kwargs.get("telegram_id", 0)
        if uid == 1001:
            return fake_a
        return fake_b

    def _fake_format(result):
        return _format_fake(result)

    with (
        patch(_MOCK_RECALL_PATH, side_effect=_fake_recall),
        patch(_MOCK_FORMAT_PATH, side_effect=_fake_format),
    ):
        from src.core.memory.prefetch_recall import (
            prefetch_recall,
            get_prefetched_recall,
        )

        # Prefetch для двух пользователей
        await prefetch_recall(1001, "запрос A")
        await prefetch_recall(2002, "запрос B")

        # Проверяем независимость
        cached_a = await get_prefetched_recall(1001)
        cached_b = await get_prefetched_recall(2002)

        assert cached_a is not None
        assert cached_b is not None
        assert "пользователя A" in cached_a["memory_context"]
        assert "пользователя B" in cached_b["memory_context"]


# ── TTL config respect ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ttl_from_config():
    """TTL кэша берётся из settings.prefetch_recall_ttl."""
    original_ttl = settings.prefetch_recall_ttl
    settings.prefetch_recall_ttl = 0.1  # 100 мс

    try:
        fake_result = _fake_recall_result(["короткий TTL"])

        with (
            patch(_MOCK_RECALL_PATH, return_value=fake_result),
            patch(_MOCK_FORMAT_PATH, return_value=_format_fake(fake_result)),
        ):
            from src.core.memory.prefetch_recall import (
                prefetch_recall,
                get_prefetched_recall,
            )

            await prefetch_recall(77777, "запрос")

            # Сразу после prefetch — должен быть доступен
            cached = await get_prefetched_recall(77777)
            assert cached is not None, "Сразу после prefetch результат должен быть"

            # Ждём больше TTL
            await asyncio.sleep(0.2)

            # Теперь должен протухнуть
            cached = await get_prefetched_recall(77777)
            assert cached is None, f"После {0.2}с (TTL={0.1}с) кэш должен протухнуть"
    finally:
        settings.prefetch_recall_ttl = original_ttl
