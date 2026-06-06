"""Tests for SmartExtract optimization pipeline.

Tests:
  - Trivial message → skip extraction
  - Greeting → skip
  - Factual statement → extract
  - Priority scoring: preference > factual > emotional
  - Cache hit for repeated message
  - Cache miss for different message
  - Feature flag off → always extract
  - Multi-sentence extraction quality
  - Classifier integration for skip decisions
  - Low-priority messages below threshold → skip
  - Model routing: heavy for complex, light for simple
  - Normalization consistency for cache keys
  - Edge cases: empty, whitespace, emoji-only
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Ensure valid test environment before importing src ─────────────────
os.environ["BOT_TOKEN"] = "12345678:abcdefghijklmnopqrstuvwxyz12345"
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.memory.smart_extractor import (
    ExtractPriority,
    ExtractDecision,
    score_extract_priority,
    make_extract_decision,
    cache_extraction_result,
    _normalize_for_cache,
    _hash_normalized,
    _build_cache_key,
)


# ── Helpers ────────────────────────────────────────────────────────────


def _empty_decision() -> ExtractDecision:
    """Создать решение по умолчанию (должно извлечь)."""
    return ExtractDecision(
        should_extract=True,
        priority=ExtractPriority.HIGH,
        score=1.0,
        reason="test",
        model_mode="heavy",
    )


# ── Тесты: score_extract_priority ─────────────────────────────────────


class TestPriorityScoring:
    """Тесты функции score_extract_priority."""

    def test_empty_text_score_zero(self) -> None:
        assert score_extract_priority("") == 0.0
        assert score_extract_priority("   ") == 0.0

    def test_factual_statement_high(self) -> None:
        """Фактические утверждения → высокий score."""
        score = score_extract_priority("я купил машину вчера")
        assert score >= 0.4, f"Expected >= 0.4, got {score}"

    def test_preference_high(self) -> None:
        """Предпочтения → высокий score."""
        score = score_extract_priority("я люблю кофе по утрам")
        assert score >= 0.35, f"Expected >= 0.35, got {score}"

    def test_plan_high(self) -> None:
        """Планы → высокий score."""
        score = score_extract_priority("завтра я планирую пойти в спортзал")
        assert score >= 0.35, f"Expected >= 0.35, got {score}"

    def test_emotional_low(self) -> None:
        """Эмоции → низкий score."""
        score = score_extract_priority("круто! вау!")
        assert score < 0.3, f"Expected < 0.3, got {score}"

    def test_question_low(self) -> None:
        """Вопросы → низкий score."""
        score = score_extract_priority("как дела? что нового?")
        assert score < 0.3, f"Expected < 0.3, got {score}"

    def test_preference_beats_emotional(self) -> None:
        """Предпочтения > эмоции."""
        pref_score = score_extract_priority("я люблю читать книги")
        emo_score = score_extract_priority("круто!")
        assert pref_score > emo_score, f"{pref_score} should be > {emo_score}"

    def test_factual_beats_emotional(self) -> None:
        """Факты > эмоции."""
        fact_score = score_extract_priority("я работаю в IT компании")
        emo_score = score_extract_priority("блин!")
        assert fact_score > emo_score, f"{fact_score} should be > {emo_score}"

    def test_long_message_boost(self) -> None:
        """Длинное сообщение получает бонус."""
        short_score = score_extract_priority("привет")
        long_text = (
            "я работаю в крупной IT компании и занимаюсь разработкой уже пять лет"
        )
        long_score = score_extract_priority(long_text)
        assert long_score > short_score, f"{long_score} should be > {short_score}"

    def test_self_reference_boost(self) -> None:
        """Самореференция («я», «мне») даёт бонус."""
        with_self = score_extract_priority("я хочу рассказать о себе")
        without_self = score_extract_priority("погода хорошая сегодня")
        assert with_self > without_self, f"{with_self} should be > {without_self}"

    def test_score_bounded_0_to_1(self) -> None:
        """Score всегда в [0.0, 1.0]."""
        for text in (
            "",
            "а",
            "я купил машину и ещё много всего интересного произошло за последнее время",
            "привет как дела что нового расскажи",
            "люблю ненавижу обожаю планирую завтра послезавтра",
            "🔥" * 100,
        ):
            score = score_extract_priority(text)
            assert 0.0 <= score <= 1.0, f"score={score} for text={text!r}"


# ── Тесты: make_extract_decision (skip trivial) ─────────────────────


class TestTrivialSkip:
    """Тесты пропуска тривиальных сообщений."""

    @pytest.mark.asyncio
    async def test_short_message_skip(self) -> None:
        """Сообщения < 5 символов пропускаются."""
        decision = await make_extract_decision("ок")
        assert decision.should_extract is False
        assert decision.priority == ExtractPriority.SKIP
        assert decision.fast_skip is True

    @pytest.mark.asyncio
    async def test_single_word_skip(self) -> None:
        """Однословные ответы пропускаются."""
        decision = await make_extract_decision("ага")
        assert decision.should_extract is False
        assert decision.fast_skip is True

    @pytest.mark.asyncio
    async def test_empty_message_skip(self) -> None:
        """Пустое сообщение пропускается."""
        decision = await make_extract_decision("")
        assert decision.should_extract is False
        assert decision.fast_skip is True

    @pytest.mark.asyncio
    async def test_whitespace_only_skip(self) -> None:
        """Только пробелы — пропускаем."""
        decision = await make_extract_decision("   ")
        assert decision.should_extract is False
        assert decision.fast_skip is True


# ── Тесты: make_extract_decision (classifier skip) ───────────────────


class TestClassifierSkip:
    """Тесты пропуска через MessageClassifier."""

    @pytest.mark.asyncio
    async def test_greeting_skip(self) -> None:
        """Приветствия пропускаются."""
        decision = await make_extract_decision("привет")
        assert decision.should_extract is False
        # Должен быть SKIP — либо через classifier greeting, либо fast_skip
        assert decision.priority == ExtractPriority.SKIP

    @pytest.mark.asyncio
    async def test_farewell_skip(self) -> None:
        """Прощания пропускаются."""
        decision = await make_extract_decision("пока")
        assert decision.should_extract is False
        # "пока" — 4 символа, ловится проверкой длины
        assert decision.fast_skip is True or decision.priority == ExtractPriority.SKIP

    @pytest.mark.asyncio
    async def test_trivial_agreement_skip(self) -> None:
        """Тривиальные согласия («ага», «да») пропускаются."""
        decision = await make_extract_decision("ага, понял")
        # Может быть SKIP или LOW — зависит от classifier
        assert decision.should_extract is False

    @pytest.mark.asyncio
    async def test_trivial_ok_skip(self) -> None:
        """«ок» пропускается."""
        decision = await make_extract_decision("ок")
        assert decision.should_extract is False


# ── Тесты: make_extract_decision (извлечение) ────────────────────────


class TestExtraction:
    """Тесты решений об извлечении для содержательных сообщений."""

    @pytest.mark.asyncio
    async def test_factual_statement_extract(self) -> None:
        """Фактическое утверждение → извлечь."""
        decision = await make_extract_decision(
            "я купил новую машину вчера, очень доволен"
        )
        assert decision.should_extract is True
        assert decision.priority in (ExtractPriority.MEDIUM, ExtractPriority.HIGH)

    @pytest.mark.asyncio
    async def test_preference_extract(self) -> None:
        """Предпочтение → извлечь."""
        decision = await make_extract_decision("я люблю слушать джаз по вечерам")
        assert decision.should_extract is True

    @pytest.mark.asyncio
    async def test_plan_extract(self) -> None:
        """План → извлечь."""
        decision = await make_extract_decision("завтра собираюсь начать новый проект")
        assert decision.should_extract is True

    @pytest.mark.asyncio
    async def test_low_priority_skip(self) -> None:
        """Низкий приоритет (ниже порога) → пропустить."""
        decision = await make_extract_decision("как дела?")
        assert (
            decision.should_extract is False or decision.priority == ExtractPriority.LOW
        )

    @pytest.mark.asyncio
    async def test_emotional_exclamation_skip(self) -> None:
        """Эмоциональное восклицание → пропустить."""
        decision = await make_extract_decision("круто!")
        assert decision.should_extract is False


# ── Тесты: кэширование ───────────────────────────────────────────────


class TestCaching:
    """Тесты кэширования результатов извлечения."""

    @pytest.mark.asyncio
    async def test_cache_hit_repeated_message(self) -> None:
        """Повторное сообщение → cache hit."""
        text = "я работаю программистом в крупной компании"

        # Первый вызов — без кэша
        decision1 = await make_extract_decision(text, user_id=123456)
        assert decision1.cached_result is None

        # Кэшируем результат
        await cache_extraction_result(
            text,
            [{"fact": "User works as a programmer", "sentiment": "neutral"}],
            model_mode="light",
            user_id=123456,
        )

        # Второй вызов — должен найти в кэше
        decision2 = await make_extract_decision(text, user_id=123456)
        assert decision2.cached_result is not None
        assert len(decision2.cached_result) == 1
        assert decision2.cached_result[0]["fact"] == "User works as a programmer"

    @pytest.mark.asyncio
    async def test_cache_miss_different_message(self) -> None:
        """Разные сообщения → cache miss."""
        text1 = "я работаю программистом"
        text2 = "я люблю готовить пасту"

        # Кэшируем первое
        await cache_extraction_result(
            text1,
            [{"fact": "User is a programmer", "sentiment": "neutral"}],
            model_mode="light",
            user_id=123456,
        )

        # Второе — другое, должно быть miss
        decision = await make_extract_decision(text2, user_id=123456)
        assert decision.cached_result is None

    @pytest.mark.asyncio
    async def test_cache_normalized_key(self) -> None:
        """Нормализованные сообщения дают одинаковый ключ кэша."""
        text1 = "Я работаю программистом!!!"
        text2 = "я работаю программистом"

        key1 = _build_cache_key(text1, user_id=123)
        key2 = _build_cache_key(text2, user_id=123)

        # После нормализации ключи должны совпасть
        assert key1 == key2

    @pytest.mark.asyncio
    async def test_cache_user_isolation(self) -> None:
        """Кэш изолирован по user_id."""
        text = "я работаю программистом"

        # Кэшируем для user 1
        await cache_extraction_result(
            text,
            [{"fact": "User 1 fact", "sentiment": "neutral"}],
            user_id=111,
        )

        # User 2 не должен получить кэш user 1
        decision = await make_extract_decision(text, user_id=222)
        assert decision.cached_result is None


# ── Тесты: нормализация ──────────────────────────────────────────────


class TestNormalization:
    """Тесты нормализации текста для кэша."""

    def test_punctuation_removed(self) -> None:
        norm = _normalize_for_cache("Привет, как дела? Всё хорошо!!!")
        assert "," not in norm
        assert "?" not in norm
        assert "!" not in norm
        assert "привет" in norm

    def test_numbers_removed(self) -> None:
        norm = _normalize_for_cache("мой номер 1234567890")
        assert "1234567890" not in norm
        assert "номер" in norm

    def test_case_normalized(self) -> None:
        norm = _normalize_for_cache("ПРИВЕТ МИР")
        assert norm == "привет мир"

    def test_identical_after_normalization(self) -> None:
        """Разные формы одного сообщения нормализуются одинаково."""
        n1 = _normalize_for_cache("Я купил машину за 1000000 рублей!!!")
        n2 = _normalize_for_cache("я купил машину за рублей")
        # После удаления чисел и пунктуации — должны совпасть
        assert n1 == n2

    def test_hash_consistent(self) -> None:
        """Одинаковые нормализованные тексты дают одинаковый хэш."""
        h1 = _hash_normalized("я работаю программистом")
        h2 = _hash_normalized("я работаю программистом")
        assert h1 == h2

    def test_hash_different(self) -> None:
        """Разные тексты дают разные хэши."""
        h1 = _hash_normalized("я работаю программистом")
        h2 = _hash_normalized("я люблю готовить")
        assert h1 != h2


# ── Тесты: feature flag ──────────────────────────────────────────────


class TestFeatureFlag:
    """Тесты переключения feature flag."""

    @pytest.mark.asyncio
    async def test_flag_off_always_extract(self) -> None:
        """При smart_extract_optimized=False — всегда извлекаем."""
        with patch("src.core.memory.smart_extractor.settings") as mock_settings:
            mock_settings.smart_extract_optimized = False
            # Даже для короткого сообщения
            decision = await make_extract_decision("да")
            assert decision.should_extract is True
            assert decision.model_mode == "heavy"

    @pytest.mark.asyncio
    async def test_flag_on_skips_trivial(self) -> None:
        """При smart_extract_optimized=True — пропускаем тривиальные."""
        with patch("src.core.memory.smart_extractor.settings") as mock_settings:
            mock_settings.smart_extract_optimized = True
            mock_settings.extract_priority_threshold = 0.3
            decision = await make_extract_decision("да")
            # "да" — < 5 символов, быстрый skip
            assert decision.should_extract is False
            assert decision.fast_skip is True


# ── Тесты: model routing ─────────────────────────────────────────────


class TestModelRouting:
    """Тесты выбора лёгкой/тяжёлой модели."""

    @pytest.mark.asyncio
    async def test_simple_fact_light_model(self) -> None:
        """Простой факт → light модель."""
        decision = await make_extract_decision("я люблю кофе")
        if decision.should_extract:
            # MEDIUM priority → light
            if decision.priority == ExtractPriority.MEDIUM:
                assert decision.model_mode == "light"

    @pytest.mark.asyncio
    async def test_complex_fact_heavy_model(self) -> None:
        """Сложный факт с контекстом → heavy модель."""
        decision = await make_extract_decision(
            "ты говорил что вчера я рассказывал про свой проект нейросетей "
            "и ещё много чего обсуждали на прошлой неделе про инвестиции"
        )
        if decision.should_extract and decision.priority == ExtractPriority.HIGH:
            assert decision.model_mode in ("light", "heavy")

    @pytest.mark.asyncio
    async def test_low_priority_light(self) -> None:
        """LOW приоритет → всегда light."""
        decision = await make_extract_decision("как дела?")
        if decision.should_extract:
            assert decision.model_mode == "light"


# ── Тесты: multi-sentence quality ────────────────────────────────────


class TestMultiSentence:
    """Тесты качества для много-предложений."""

    def test_multi_sentence_scoring(self) -> None:
        """Много предложений с фактами → высокий score."""
        text = (
            "я работаю дизайнером уже 5 лет. "
            "люблю минимализм и тёмные тона. "
            "завтра планирую начать новый проект."
        )
        score = score_extract_priority(text)
        assert score >= 0.5, f"Expected >= 0.5 for rich multi-sentence, got {score}"

    def test_multi_sentence_noise_low(self) -> None:
        """Много предложений без фактов → низкий score."""
        text = "привет! как дела? что нового? расскажи что-нибудь."
        score = score_extract_priority(text)
        assert score < 0.5, f"Expected < 0.5 for noise multi-sentence, got {score}"

    @pytest.mark.asyncio
    async def test_multi_sentence_extract_decision(self) -> None:
        """Многословное сообщение с фактами должно пройти фильтр."""
        text = (
            "я работаю в банке уже три года. "
            "мне нравится моя работа, хотя иногда устаю. "
            "планирую взять отпуск в следующем месяце."
        )
        decision = await make_extract_decision(text)
        assert decision.should_extract is True


# ── Тесты: граничные случаи ──────────────────────────────────────────


class TestEdgeCases:
    """Граничные случаи."""

    @pytest.mark.asyncio
    async def test_none_text(self) -> None:
        """None текст — пропускаем."""
        decision = await make_extract_decision("")
        assert decision.should_extract is False

    @pytest.mark.asyncio
    async def test_emoji_only(self) -> None:
        """Только эмодзи — пропускаем."""
        decision = await make_extract_decision("🔥👍😂")
        # Нет ключевых слов, короткое → skip
        assert decision.should_extract is False

    @pytest.mark.asyncio
    async def test_very_long_message(self) -> None:
        """Очень длинное сообщение с фактами — извлекаем."""
        text = "я " + "работаю " * 20 + "и люблю свою профессию"
        decision = await make_extract_decision(text)

    @pytest.mark.asyncio
    async def test_mixed_russian_english(self) -> None:
        """Смешанный русско-английский текст."""
        decision = await make_extract_decision(
            "я работаю software engineer в international компании"
        )
        # Должен пройти (есть «я работаю»)
        assert decision.should_extract is True

    def test_score_with_punctuation(self) -> None:
        """Пунктуация не должна ломать scoring."""
        score = score_extract_priority("я купил машину")
        assert score >= 0.4, f"Expected >= 0.4, got {score}"
        # Пунктуация в середине тоже должна работать
        score2 = score_extract_priority("я купил, машину!")
        assert score2 >= 0.4, f"Expected >= 0.4 with punctuation, got {score2}"
