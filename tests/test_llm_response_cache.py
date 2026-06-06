"""Tests for LLM Response Cache (SmartCache) — двухуровневый кэш ответов LLM.

Покрытие:
- Exact match cache hit
- Semantic match для разных приветствий (greetings → blanket-ключ)
- Cache miss для контекстно-зависимых запросов
- should_cache возвращает False для ссылок на прошлое
- TTL expiry
- Нормализация: имена → {NAME}, числа → {NUM}, даты → {DATE}, URL → {URL}
- Feature flag off → кэширование отключено
- Cache bypass для команд (начинаются с /)
- URL-содержащие запросы не кэшируются
- Сложные многосоставные вопросы не кэшируются
- Простые вопросы кэшируются
- should_cache True для приветствий
- should_cache False для @упоминаний
- Semantic match для разных прощаний (farewell → blanket-ключ)
- Cache set/get roundtrip с classifier_result
- Пустой текст не кэшируется
- Статистика кэша
"""

import asyncio
import os
import time
import sys

import pytest

# Env vars MUST be set before importing from src
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:TEST_TOKEN_HERE_abcdefghijklmnopqrstuvwx"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.core.cache.manager import (
    _reset_cache_manager_for_test,
)
from src.core.intelligence.llm_response_cache import (
    LLMResponseCache,
    _reset_response_cache_for_test,
)
from src.config import settings

# Доступ к синглтону через модуль (не через локальную переменную),
# чтобы fixture мог обновить модульный атрибут и все обращения
# получали актуальный инстанс.
import src.core.intelligence.llm_response_cache as _rc_mod


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_caches():
    """Reset caches before each test for isolation."""
    _reset_cache_manager_for_test()
    _reset_response_cache_for_test()
    yield
    _reset_cache_manager_for_test()
    _reset_response_cache_for_test()


def _make_result(**overrides) -> dict[str, bool]:
    """Build a classifier_result dict with defaults (all False)."""
    base = {
        "greeting": False,
        "farewell": False,
        "gratitude": False,
        "question": False,
        "command": False,
        "agreement": False,
        "disagreement": False,
        "emotion": False,
        "profanity": False,
        "trivial": False,
        "needs_routing": False,
        "needs_llm": True,
    }
    base.update(overrides)
    return base


# ── Exact match ────────────────────────────────────────────────────────


class TestExactMatch:
    """Тесты точного совпадения (Tier 1)."""

    @pytest.mark.asyncio
    async def test_exact_match_cache_hit(self):
        """Точное совпадение текста возвращает закэшированный ответ."""
        text = "Привет, как дела?"
        response = "Привет! Всё отлично, спасибо!"
        await _rc_mod.response_cache.set(text, response)
        cached = await _rc_mod.response_cache.get(text)
        assert cached == response

    @pytest.mark.asyncio
    async def test_exact_match_case_insensitive(self):
        """Точное совпадение регистронезависимо (ключ нормализуется)."""
        text1 = "Привет, Как Дела?"
        text2 = "привет, как дела?"
        response = "Привет!"
        await _rc_mod.response_cache.set(text1, response)
        cached = await _rc_mod.response_cache.get(text2)
        assert cached == response

    @pytest.mark.asyncio
    async def test_exact_match_whitespace_normalized(self):
        """Лишние пробелы в начале/конце не влияют на exact match."""
        text1 = "  Привет  "
        text2 = "Привет"
        response = "Здравствуй!"
        await _rc_mod.response_cache.set(text1, response)
        cached = await _rc_mod.response_cache.get(text2)
        assert cached == response

    @pytest.mark.asyncio
    async def test_exact_match_miss_different_text(self):
        """Разный текст — cache miss."""
        await _rc_mod.response_cache.set("Как дела?", "Нормально")
        cached = await _rc_mod.response_cache.get("Что нового?")
        assert cached is None


# ── Semantic match ─────────────────────────────────────────────────────


class TestSemanticMatch:
    """Тесты семантического совпадения (Tier 2)."""

    @pytest.mark.asyncio
    async def test_different_greetings_same_response(self):
        """Разные приветствия → один blanket-ключ → cache hit."""
        result = _make_result(greeting=True, trivial=True)
        await _rc_mod.response_cache.set(
            "Привет!", "Здравствуй!", classifier_result=result
        )
        # Другое приветствие — должно попасть в semantic cache
        # "Здравствуй!" классифицируется как greeting (в wordlist)
        cached = await _rc_mod.response_cache.get("Здравствуй!")
        assert cached == "Здравствуй!"

    @pytest.mark.asyncio
    async def test_different_farewells_same_response(self):
        """Разные прощания → один blanket-ключ."""
        result = _make_result(farewell=True, trivial=True)
        await _rc_mod.response_cache.set(
            "Пока!", "До встречи!", classifier_result=result
        )
        cached = await _rc_mod.response_cache.get("До свидания!")
        assert cached == "До встречи!"

    @pytest.mark.asyncio
    async def test_semantic_match_question_normalized(self):
        """Вопросы с одинаковой нормализованной структурой → cache hit."""
        result = _make_result(question=True)
        await _rc_mod.response_cache.set(
            "Сколько будет 5 плюс 3?",
            "Будет 8",
            classifier_result=result,
        )
        # Тот же вопрос с другими числами → нормализация заменит числа на {NUM}
        cached = await _rc_mod.response_cache.get(
            "Сколько будет 10 плюс 7?",
            classifier_result=_make_result(question=True),
        )
        # После нормализации оба становятся: "сколько будет {num} плюс {num}"
        assert cached == "Будет 8"

    @pytest.mark.asyncio
    async def test_semantic_match_different_questions(self):
        """Разные по структуре вопросы → разные normalized hash → cache miss."""
        result = _make_result(question=True)
        await _rc_mod.response_cache.set(
            "Как дела?", "Отлично!", classifier_result=result
        )
        cached = await _rc_mod.response_cache.get(
            "Который час?", classifier_result=_make_result(question=True)
        )
        assert cached is None


# ── should_cache ───────────────────────────────────────────────────────


class TestShouldCache:
    """Тесты метода should_cache."""

    @pytest.mark.asyncio
    async def test_greeting_cacheable(self):
        """Приветствия можно кэшировать."""
        result = _make_result(greeting=True, trivial=True)
        assert _rc_mod.response_cache.should_cache("Привет!", result) is True

    @pytest.mark.asyncio
    async def test_command_not_cacheable(self):
        """Команды (начинаются с /) не кэшируются."""
        result = _make_result(command=True, needs_routing=True)
        assert _rc_mod.response_cache.should_cache("/start", result) is False

    @pytest.mark.asyncio
    async def test_past_reference_not_cacheable(self):
        """Ссылки на прошлое не кэшируются."""
        result = _make_result(question=True)
        assert (
            _rc_mod.response_cache.should_cache(
                "Ты помнишь, что я говорил вчера?", result
            )
            is False
        )

    @pytest.mark.asyncio
    async def test_url_not_cacheable(self):
        """Запросы с URL не кэшируются."""
        result = _make_result(question=True)
        assert (
            _rc_mod.response_cache.should_cache("Что на https://example.com?", result)
            is False
        )

    @pytest.mark.asyncio
    async def test_mention_not_cacheable(self):
        """@упоминания не кэшируются."""
        result = _make_result(question=True)
        assert _rc_mod.response_cache.should_cache("Привет @username!", result) is False

    @pytest.mark.asyncio
    async def test_complex_multi_clause_not_cacheable(self):
        """Сложные многосоставные вопросы не кэшируются."""
        result = _make_result(question=True)
        text = (
            "Расскажи мне про историю России. "
            "И ещё я хочу узнать про культуру Японии. "
            "А также что там с экономикой Китая происходит сейчас?"
        )
        assert _rc_mod.response_cache.should_cache(text, result) is False

    @pytest.mark.asyncio
    async def test_simple_question_cacheable(self):
        """Простые вопросы (<10 слов, 1 предложение) кэшируются."""
        result = _make_result(question=True)
        assert _rc_mod.response_cache.should_cache("Как дела?", result) is True

    @pytest.mark.asyncio
    async def test_date_reference_not_cacheable(self):
        """Запросы с датами (контекстная зависимость) не кэшируются."""
        result = _make_result(question=True)
        assert _rc_mod.response_cache.should_cache("Что было вчера?", result) is False

    @pytest.mark.asyncio
    async def test_empty_text_not_cacheable(self):
        """Пустой текст не кэшируется."""
        result = _make_result()
        assert _rc_mod.response_cache.should_cache("", result) is False
        assert _rc_mod.response_cache.should_cache("   ", result) is False

    @pytest.mark.asyncio
    async def test_agreement_cacheable(self):
        """Согласие можно кэшировать."""
        result = _make_result(agreement=True, trivial=True)
        assert _rc_mod.response_cache.should_cache("Да, согласен", result) is True

    @pytest.mark.asyncio
    async def test_disagreement_cacheable(self):
        """Несогласие можно кэшировать."""
        result = _make_result(disagreement=True, trivial=True)
        assert _rc_mod.response_cache.should_cache("Нет, не хочу", result) is True

    @pytest.mark.asyncio
    async def test_gratitude_cacheable(self):
        """Благодарность можно кэшировать."""
        result = _make_result(gratitude=True, trivial=True)
        assert _rc_mod.response_cache.should_cache("Спасибо большое!", result) is True


# ── Feature flag ───────────────────────────────────────────────────────


class TestFeatureFlag:
    """Тесты feature flag (response_cache_enabled)."""

    @pytest.mark.asyncio
    async def test_flag_off_skips_get(self):
        """Когда флаг выключен — get всегда возвращает None."""
        settings.response_cache_enabled = False
        try:
            await _rc_mod.response_cache.set("Привет!", "Здравствуй!")
            cached = await _rc_mod.response_cache.get("Привет!")
            assert cached is None
        finally:
            settings.response_cache_enabled = True

    @pytest.mark.asyncio
    async def test_flag_off_skips_set(self):
        """Когда флаг выключен — set не сохраняет."""
        settings.response_cache_enabled = False
        try:
            await _rc_mod.response_cache.set("Привет!", "Здравствуй!")
            settings.response_cache_enabled = True
            cached = await _rc_mod.response_cache.get("Привет!")
            assert cached is None  # ничего не сохранилось
        finally:
            settings.response_cache_enabled = True


# ── Normalization ──────────────────────────────────────────────────────


class TestNormalization:
    """Тесты метода _normalize."""

    def test_names_replaced(self):
        """Имена заменяются на {NAME}."""
        result = _rc_mod.response_cache._normalize("Привет, Саша!")
        assert "{NAME}" in result
        assert "саша" not in result

    def test_numbers_replaced(self):
        """Числа заменяются на {NUM}."""
        result = _rc_mod.response_cache._normalize("У меня 5 яблок и 10 груш")
        assert result.count("{NUM}") >= 2
        assert "5" not in result
        assert "10" not in result

    def test_dates_replaced(self):
        """Даты заменяются на {DATE}."""
        result = _rc_mod.response_cache._normalize("Что случилось 15 марта?")
        assert "{DATE}" in result

    def test_urls_replaced(self):
        """URL заменяются на {URL}."""
        result = _rc_mod.response_cache._normalize("Посмотри https://example.com/page")
        assert "{URL}" in result
        assert "example.com" not in result

    def test_punctuation_stripped(self):
        """Пунктуация удаляется."""
        result = _rc_mod.response_cache._normalize("Привет, как дела?!")
        # После нормализации пунктуация заменена пробелами и схлопнута
        assert "!" not in result
        assert "?" not in result

    def test_mixed_normalization(self):
        """Комплексная нормализация: имена, числа, даты, URL."""
        text = "Саша, 5 января 2024 я купил 3 апельсина на https://shop.ru"
        result = _rc_mod.response_cache._normalize(text)
        assert "{NAME}" in result
        assert "{DATE}" in result
        assert "{NUM}" in result  # числа 5, 2024, 3
        assert "{URL}" in result
        assert "саша" not in result


# ── TTL ────────────────────────────────────────────────────────────────


class TestTTL:
    """Тесты TTL (время жизни кэша)."""

    @pytest.mark.asyncio
    async def test_ttl_expiry_exact_match(self):
        """Запись с истёкшим TTL не возвращается."""
        # Устанавливаем с очень коротким TTL
        cache = _rc_mod.response_cache._cache
        await cache.set("exact:test_ttl_key", "cached_value", ttl=0.01)
        # Ждём истечения
        await asyncio.sleep(0.05)
        val = await cache.get("exact:test_ttl_key")
        assert val is None

    @pytest.mark.asyncio
    async def test_ttl_not_expired(self):
        """Запись с достаточным TTL возвращается."""
        await _rc_mod.response_cache.set("Привет!", "Здравствуй!")
        # Сразу после установки — должен быть доступен
        cached = await _rc_mod.response_cache.get("Привет!")
        assert cached == "Здравствуй!"


# ── Roundtrip with classifier_result ───────────────────────────────────


class TestRoundtrip:
    """Тесты полного цикла set/get с classifier_result."""

    @pytest.mark.asyncio
    async def test_set_get_with_classifier_result(self):
        """Установка и получение с явным classifier_result."""
        result = _make_result(greeting=True, trivial=True)
        await _rc_mod.response_cache.set(
            "Привет!", "Здравствуй!", classifier_result=result
        )
        cached = await _rc_mod.response_cache.get(
            "Доброе утро!", classifier_result=result
        )
        assert cached == "Здравствуй!"

    @pytest.mark.asyncio
    async def test_get_skips_non_cacheable(self):
        """get не возвращает результат для не-кэшируемых запросов
        (даже если exact-ключ совпадает — потому что set их не сохраняет)."""
        result = _make_result(command=True, needs_routing=True)
        await _rc_mod.response_cache.set(
            "/help", "Список команд", classifier_result=result
        )
        cached = await _rc_mod.response_cache.get("/help", classifier_result=result)
        assert cached is None  # команды не кэшируются


# ── Edge cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
    """Граничные случаи."""

    @pytest.mark.asyncio
    async def test_empty_response_not_cached(self):
        """Пустой ответ не кэшируется."""
        await _rc_mod.response_cache.set("Привет!", "")
        cached = await _rc_mod.response_cache.get("Привет!")
        assert cached is None

    @pytest.mark.asyncio
    async def test_very_long_text_truncated(self):
        """Длинный текст обрезается до 200 символов для exact-ключа."""
        long_text = "Привет! " * 100  # ~900 символов
        response = "Здравствуй!"
        await _rc_mod.response_cache.set(long_text, response)
        # Поиск по тому же длинному тексту должен сработать
        cached = await _rc_mod.response_cache.get(long_text)
        assert cached == response

    @pytest.mark.asyncio
    async def test_stats_returns_data(self):
        """stats() возвращает осмысленные данные."""
        await _rc_mod.response_cache.set("Привет!", "Здравствуй!")
        stats = await _rc_mod.response_cache.stats()
        assert "llm_response_cache" in stats
        assert "response_cache_enabled" in stats
        assert stats["response_cache_enabled"] is True

    @pytest.mark.asyncio
    async def test_reset_clears_cache(self):
        """reset_for_test очищает кэш."""
        await _rc_mod.response_cache.set("Привет!", "Здравствуй!")
        await _rc_mod.response_cache.reset_for_test()
        cached = await _rc_mod.response_cache.get("Привет!")
        assert cached is None

    @pytest.mark.asyncio
    async def test_count_sentences(self):
        """_count_sentences корректно считает предложения."""
        assert _rc_mod.response_cache._count_sentences("Привет.") == 1
        assert _rc_mod.response_cache._count_sentences("Привет! Как дела?") == 2
        assert _rc_mod.response_cache._count_sentences("Одно. Два! Три?") == 3


# ── Integration with real classifier ───────────────────────────────────


class TestWithRealClassifier:
    """Тесты с реальным MessageClassifier (проверяем, что ленивый импорт работает)."""

    @pytest.mark.asyncio
    async def test_real_classifier_greeting(self):
        """Реальный классификатор определяет greeting → blanket-ключ."""
        await _rc_mod.response_cache.set("Привет!", "Здравствуй!")
        # "Здравствуй!" классифицируется как greeting
        cached = await _rc_mod.response_cache.get("Здравствуй!")
        assert cached == "Здравствуй!"

    @pytest.mark.asyncio
    async def test_real_classifier_farewell(self):
        """Реальный классификатор определяет farewell → blanket-ключ."""
        await _rc_mod.response_cache.set("Пока!", "До встречи!")
        cached = await _rc_mod.response_cache.get("До свидания!")
        assert cached == "До встречи!"

    @pytest.mark.asyncio
    async def test_real_classifier_command_skipped(self):
        """Реальный классификатор: команды не кэшируются."""
        await _rc_mod.response_cache.set("/settings", "Вот настройки")
        cached = await _rc_mod.response_cache.get("/settings")
        assert cached is None  # команды не кэшируются
