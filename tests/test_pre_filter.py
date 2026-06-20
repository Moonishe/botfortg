"""Тесты эвристического pre-filter транскриптов."""

from __future__ import annotations


import pytest

# Добавляем корень проекта в path

from src.core.memory.pre_filter import score_transcript, should_extract


# ── Граничные случаи ──────────────────────────────────────────────


def test_empty_transcript_returns_zero():
    """Пустой транскрипт → score 0."""
    assert score_transcript("") == 0.0


def test_none_transcript_returns_zero():
    """None-подобный пустой ввод → score 0."""
    # Защита от случайной передачи None как строки
    assert score_transcript("") == 0.0


def test_short_ok_returns_zero():
    """«Ок» (2 символа) — слишком короткий, штраф -0.5 → 0."""
    score = score_transcript("ок")
    assert score == 0.0
    assert should_extract("ок") is False


# ── Шумовые транскрипты (должны быть ниже порога) ────────────────


def test_greeting_below_threshold():
    """«Привет, как дела?» — короткий и шумный → score < 0.3."""
    text = "Привет, как дела?"
    score = score_transcript(text)
    assert score < 0.3, f"expected < 0.3, got {score}"
    assert should_extract(text) is False


def test_should_extract_false_for_noise():
    """should_extract возвращает False для шумовых транскриптов."""
    assert should_extract("ага ок лол") is False
    assert should_extract("привет хай 👍") is False


# ── Факт-насыщенные транскрипты (должны быть выше порога) ────────


def test_self_ref_and_state_verbs_high_score():
    """«Я работаю в Яндексе, живу в Москве» — self-ref + 2 state verb → > 0.5."""
    text = "Я работаю в Яндексе, живу в Москве"
    score = score_transcript(text)
    assert score > 0.5, f"expected > 0.5, got {score}"
    assert should_extract(text) is True


def test_name_pattern_boosts_score():
    """«Маша Иванова сказала что…» — наличие Имя+Фамилия даёт +0.2 к score."""
    text = "Маша Иванова сказала что завтра будет в офисе после обеда"
    score = score_transcript(text)
    # Self-ref нет, state verb «будет» тоже не в списке, name +0.2, event +0.2,
    # len > 50 +0.1 → минимум 0.5
    assert score >= 0.4, f"expected name boost, got {score}"


def test_negation_preference_high_score():
    """«Я не люблю острое, предпочитаю мягкую пищу» — self-ref + «не люблю» → высокий score."""
    text = "Я не люблю острое, предпочитаю мягкую пищу и спокойную музыку"
    score = score_transcript(text)
    assert score > 0.5, f"expected > 0.5, got {score}"
    assert should_extract(text) is True


def test_event_markers_boost_score():
    """Дата/день недели в транскрипте → +0.2 к score."""
    text = "Завтра встречаемся в 19:00 на Невском, я буду с ноутбуком"
    score = score_transcript(text)
    # self-ref «я» +0.3, event «завтра» +0.2, len > 50 +0.1, number «19» +0.1 = 0.7
    assert score > 0.5, f"expected > 0.5, got {score}"


# ── Граничные свойства score ─────────────────────────────────────


def test_score_is_clamped_to_unit_interval():
    """Score всегда в [0.0, 1.0]."""
    # Много штрафов: короткий + шумный → должно clamp'нуться к 0
    assert 0.0 <= score_transcript("ок") <= 1.0
    # Много бонусов: self-ref + state + event + name + len + number → clamp к 1
    mega = (
        "Я работаю и живу в Москве уже 5 лет, люблю своих детей, "
        "Анна Петрова сказала что завтра 15 марта мы встречаемся в офисе"
    )
    assert 0.0 <= score_transcript(mega) <= 1.0
    assert score_transcript(mega) == 1.0  # 0.3+0.3+0.2+0.2+0.1+0.1 = 1.2 → clamp 1.0


def test_should_extract_threshold_respected():
    """should_extract с кастомным min_score."""
    # Достаточно длинный транскрипт: self-ref + state verb + len>50 + number → 0.8
    text = "Я работаю в офисе уже 5 лет, занимаюсь разработкой на Python"
    assert should_extract(text, min_score=0.3) is True
    assert should_extract(text, min_score=0.5) is True
    assert should_extract(text, min_score=0.9) is False
    assert should_extract(text, min_score=0.0) is True
    assert should_extract(text, min_score=1.5) is False  # невозможно достичь


def test_pure_noise_with_number_passes():
    """Если в шумовом транскрипте есть число и он длинный — score может вырасти."""
    text = "привет, сегодня 5 раз заказывал пиццу и всё равно голодный"  # noqa: E501
    # len > 50, есть число → 0.1+0.1, но «привет» в шуме, 1 из 9 слов → 11% < 30%
    # → нет штрафа за шум. Итог: 0.2
    score = score_transcript(text)
    assert 0.0 < score <= 1.0


def test_thanks_greeting_alone_filtered():
    """«Привет!» — короткий и шумный, должен быть отфильтрован."""
    assert should_extract("Привет!") is False
    assert should_extract("Хай") is False
    assert should_extract("👍") is False


def test_only_name_is_filtered():
    """Только имя без контекста — name bonus один, 0.2 < 0.3, не пройдёт."""
    text = "Андрей Петров"  # name +0.2, длина 13 → -0.5 штраф → 0
    score = score_transcript(text)
    assert score == 0.0
