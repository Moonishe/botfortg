"""Tests for SmartModelRouter — выбор лёгкой/тяжёлой модели по сложности запроса."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Ensure valid test environment before importing src ─────────────────
os.environ["BOT_TOKEN"] = "12345678:abcdefghijklmnopqrstuvwxyz12345"
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.core.intelligence.smart_router import (
    SmartModelRouter,
    get_router,
    _calc_complexity,
    _COMPLEXITY_THRESHOLD,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def router() -> SmartModelRouter:
    """Глобальный экземпляр SmartModelRouter."""
    return SmartModelRouter()


@pytest.fixture
def fresh_router() -> SmartModelRouter:
    """Свежий экземпляр (пересоздаётся для каждого теста)."""
    return SmartModelRouter()


# ── Моки classifier_result для _calc_complexity ──────────────────────


def _empty_classifier() -> dict[str, bool]:
    return {
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
    }


# ── Тесты: force-режимы ──────────────────────────────────────────────


class TestForceMode:
    """Принудительный режим — без эвристики."""

    def test_force_light(self, fresh_router: SmartModelRouter) -> None:
        result = fresh_router.route(
            "сложный запрос с анализом данных и отсылкой к прошлому", mode="light"
        )
        assert result == "light"

    def test_force_heavy(self, fresh_router: SmartModelRouter) -> None:
        result = fresh_router.route("привет", mode="heavy")
        assert result == "heavy"


# ── Тесты: простые сообщения → light ─────────────────────────────────


class TestSimpleMessages:
    """Простые сообщения должны уходить на лёгкую модель."""

    def test_simple_greeting_light(self, fresh_router: SmartModelRouter) -> None:
        """Приветствие → light."""
        result = fresh_router.route("привет", mode="auto")
        assert result == "light"

    def test_simple_farewell_light(self, fresh_router: SmartModelRouter) -> None:
        """Прощание → light."""
        result = fresh_router.route("пока", mode="auto")
        assert result == "light"

    def test_how_are_you_light(self, fresh_router: SmartModelRouter) -> None:
        """«как дела?» → light."""
        result = fresh_router.route("как дела?", mode="auto")
        assert result == "light"

    def test_what_time_light(self, fresh_router: SmartModelRouter) -> None:
        """«сколько времени?» → короткий вопрос → light."""
        result = fresh_router.route("сколько времени?", mode="auto")
        assert result == "light"

    def test_thanks_light(self, fresh_router: SmartModelRouter) -> None:
        """«спасибо» → light."""
        result = fresh_router.route("спасибо", mode="auto")
        assert result == "light"

    def test_ok_light(self, fresh_router: SmartModelRouter) -> None:
        """«ок» → light (тривиальное)."""
        result = fresh_router.route("ок", mode="auto")
        assert result == "light"

    def test_greeting_with_exclamation_light(
        self, fresh_router: SmartModelRouter
    ) -> None:
        """«привет! как твои дела? что нового?» → greeting trigger → light."""
        result = fresh_router.route("привет! как твои дела? что нового?", mode="auto")
        assert result == "light"

    def test_trivial_ok_light(self, fresh_router: SmartModelRouter) -> None:
        """«ага, понял» → trivial → light."""
        result = fresh_router.route("ага, понял", mode="auto")
        assert result == "light"


# ── Тесты: сложные сообщения → heavy ─────────────────────────────────


class TestComplexMessages:
    """Сложные сообщения должны уходить на тяжёлую модель."""

    def test_past_reference_heavy(self, fresh_router: SmartModelRouter) -> None:
        """Отсылка к прошлому → heavy."""
        result = fresh_router.route(
            "расскажи подробнее про проект Neurobench, который мы обсуждали вчера",
            mode="auto",
        )
        assert result == "heavy"

    def test_what_i_said_heavy(self, fresh_router: SmartModelRouter) -> None:
        """«напомни что я говорил про встречу на прошлой неделе» → heavy."""
        result = fresh_router.route(
            "напомни что я говорил про встречу на прошлой неделе",
            mode="auto",
        )
        assert result == "heavy"

    def test_do_you_remember_heavy(self, fresh_router: SmartModelRouter) -> None:
        """«помнишь наш разговор про...» → heavy (past ref + enough words)."""
        result = fresh_router.route(
            "помнишь наш разговор про инвестиции? расскажи что мы тогда решили",
            mode="auto",
        )
        assert result == "heavy"

    def test_you_said_heavy(self, fresh_router: SmartModelRouter) -> None:
        """«ты говорил что...» → heavy (past ref + multi-clause)."""
        result = fresh_router.route(
            "ты говорил что поможешь с отчётом, как продвигается работа над ним?",
            mode="auto",
        )
        assert result == "heavy"

    def test_long_multi_sentence_heavy(self, fresh_router: SmartModelRouter) -> None:
        """Длинное сообщение с несколькими предложениями → heavy."""
        result = fresh_router.route(
            "Мне нужно проанализировать данные за последний месяц. "
            "Особенно интересует динамика продаж и сравнение с прошлым кварталом. "
            "Также хочу понять какие клиенты принесли больше всего выручки. "
            "Подготовь, пожалуйста, сводку с выводами и рекомендациями.",
            mode="auto",
        )
        assert result == "heavy"

    def test_multi_question_heavy(self, fresh_router: SmartModelRouter) -> None:
        """Несколько вопросительных знаков → heavy."""
        result = fresh_router.route(
            "какие планы на завтра? что с проектом? когда дедлайн?",
            mode="auto",
        )
        assert result == "heavy"

    def test_with_url_heavy(self, fresh_router: SmartModelRouter) -> None:
        """Сообщение с URL → heavy."""
        result = fresh_router.route(
            "проанализируй эту статью https://example.com/article и расскажи что думаешь",
            mode="auto",
        )
        assert result == "heavy"

    def test_with_dates_heavy(self, fresh_router: SmartModelRouter) -> None:
        """Сообщение с датами → heavy."""
        result = fresh_router.route(
            "напомни про встречу 15.06.2026 в 14:00",
            mode="auto",
        )
        assert result == "heavy"

    def test_with_mention_heavy(self, fresh_router: SmartModelRouter) -> None:
        """Сообщение с @mention → heavy."""
        result = fresh_router.route(
            "напиши @username что я буду позже",
            mode="auto",
        )
        assert result == "heavy"


# ── Тесты: граничные случаи ──────────────────────────────────────────


class TestEdgeCases:
    """Граничные случаи."""

    def test_empty_text_light(self, fresh_router: SmartModelRouter) -> None:
        """Пустой текст → light."""
        result = fresh_router.route("", mode="auto")
        assert result == "light"

    def test_whitespace_only_light(self, fresh_router: SmartModelRouter) -> None:
        """Только пробелы → light."""
        result = fresh_router.route("   ", mode="auto")
        assert result == "light"

    def test_very_short_light(self, fresh_router: SmartModelRouter) -> None:
        """Очень короткое сообщение → light."""
        result = fresh_router.route("да", mode="auto")
        assert result == "light"

    def test_emotional_exclamation_light(self, fresh_router: SmartModelRouter) -> None:
        """Эмоциональное восклицание → light."""
        result = fresh_router.route("ура!!!", mode="auto")
        assert result == "light"


# ── Тесты: команды → heavy ───────────────────────────────────────────


class TestCommands:
    """Команды, требующие reasoning → heavy."""

    def test_search_command_heavy(self, fresh_router: SmartModelRouter) -> None:
        """«найди все сообщения от...» → команда → heavy."""
        result = fresh_router.route(
            "найди все сообщения от Олега за последнюю неделю",
            mode="auto",
        )
        assert result == "heavy"

    def test_summarize_command_heavy(self, fresh_router: SmartModelRouter) -> None:
        """«проанализируй это» → команда → heavy."""
        result = fresh_router.route(
            "проанализируй этот текст и сделай выводы",
            mode="auto",
        )
        assert result == "heavy"


# ── Тесты: функция _calc_complexity ──────────────────────────────────


class TestComplexityScoring:
    """Тесты эвристической оценки сложности."""

    def test_empty_text_score_zero(self) -> None:
        score = _calc_complexity("", _empty_classifier())
        assert score == 0

    def test_greeting_score_low(self) -> None:
        score = _calc_complexity("привет", _empty_classifier())
        assert score < _COMPLEXITY_THRESHOLD

    def test_past_reference_high_score(self) -> None:
        score = _calc_complexity(
            "ты говорил что вчера будет встреча и ещё много всего обсуждали на прошлой неделе",
            _empty_classifier(),
        )
        assert score >= _COMPLEXITY_THRESHOLD

    def test_long_text_high_score(self) -> None:
        # Длинный текст + несколько предложений + дата → > 30
        text = (
            "очень длинный запрос с большим количеством слов. "
            "а также со вторым предложением. и третьим предложением. "
            "нужно проанализировать данные за 15.06.2026 и сделать выводы."
        )
        score = _calc_complexity(text, _empty_classifier())
        assert score >= _COMPLEXITY_THRESHOLD

    def test_trivial_classification_reduces_score(self) -> None:
        moderate_text = "да, хорошо, договорились спасибо"
        cls_result = {
            **_empty_classifier(),
            "trivial": True,
        }
        score = _calc_complexity(moderate_text, cls_result)
        # Должно быть ниже порога из-за trivial override
        assert score < _COMPLEXITY_THRESHOLD

    def test_command_classification_boosts_score(self) -> None:
        cls_result = {
            **_empty_classifier(),
            "command": True,
        }
        score = _calc_complexity("найди это", cls_result)
        assert score >= _COMPLEXITY_THRESHOLD

    def test_score_bounded_0_100(self) -> None:
        """Score всегда в [0, 100]."""
        for text in ("привет", "а" * 1000, "ты говорил " * 50, "", "ок"):
            score = _calc_complexity(text, _empty_classifier())
            assert 0 <= score <= 100, f"score={score} for text={text!r}"


# ── Тесты: синглтон get_router ──────────────────────────────────────


class TestSingleton:
    """Синглтон get_router."""

    def test_same_instance(self) -> None:
        r1 = get_router()
        r2 = get_router()
        assert r1 is r2

    def test_is_available(self) -> None:
        r = get_router()
        assert r.is_available is True


# ── Тесты: mode=auto (по умолчанию) ──────────────────────────────────


class TestAutoModeDefault:
    """mode='auto' используется по умолчанию."""

    def test_auto_is_default(self, fresh_router: SmartModelRouter) -> None:
        """Без явного mode — auto."""
        result = fresh_router.route("привет")
        assert result == "light"

    def test_auto_heavy_complex(self, fresh_router: SmartModelRouter) -> None:
        """Без явного mode — auto, сложный запрос → heavy."""
        result = fresh_router.route(
            "расскажи подробнее про проект Neurobench, который мы обсуждали вчера"
        )
        assert result == "heavy"
