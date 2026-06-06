"""Tests for pre_gate.py — pattern matching and response resolution.

Covers: all categories, exact matches, substring matches, case insensitivity,
feature flag behavior, and edge cases.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Override env vars set by conftest.py with valid-format token
os.environ["BOT_TOKEN"] = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ-abc123def456"
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")

import pytest
from src.core.intelligence.pre_gate import check_pre_gate, get_pattern_stats


# ── Category: Time Greetings ────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("привет", "Привет! Чем могу помочь?"),
        ("Доброе утро", "Привет! Чем могу помочь?"),
        ("добрый день!", "Привет! Чем могу помочь?"),
        ("Добрый вечер", "Привет! Чем могу помочь?"),
        ("хай", "Привет! Чем могу помочь?"),
        ("hello", "Привет! Чем могу помочь?"),
        ("ку", "Привет! Чем могу помочь?"),
        ("здарова", "Привет! Чем могу помочь?"),
        ("здравствуйте", "Привет! Чем могу помочь?"),
        ("приветик", "Привет! Чем могу помочь?"),
        ("Привет!", "Привет! Чем могу помочь?"),  # Trailing punctuation
        ("  привет  ", "Привет! Чем могу помочь?"),  # Whitespace
    ],
)
def test_greetings(text: str, expected: str) -> None:
    """Time-of-day and general greetings return standard greeting response."""
    assert check_pre_gate(text) == expected


# ── Category: Farewells ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("пока", "До связи! Если что — я здесь."),
        ("до свидания", "До связи! Если что — я здесь."),
        ("спокойной ночи", "До связи! Если что — я здесь."),
        ("bye", "До связи! Если что — я здесь."),
        ("до встречи", "До связи! Если что — я здесь."),
        ("пока-пока", "До связи! Если что — я здесь."),
    ],
)
def test_farewells(text: str, expected: str) -> None:
    """Farewell phrases return standard farewell response."""
    assert check_pre_gate(text) == expected


# ── Category: Gratitude ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("спасибо", "Всегда пожалуйста! Рад помочь."),
        ("благодарю", "Всегда пожалуйста! Рад помочь."),
        ("thanks", "Всегда пожалуйста! Рад помочь."),
        ("спс", "Всегда пожалуйста! Рад помочь."),
        ("пасиб", "Всегда пожалуйста! Рад помочь."),
        ("мерси", "Всегда пожалуйста! Рад помочь."),
        ("дякую", "Всегда пожалуйста! Рад помочь."),
    ],
)
def test_gratitude(text: str, expected: str) -> None:
    """Gratitude expressions return polite acknowledgment."""
    assert check_pre_gate(text) == expected


# ── Category: Agreement ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "да",
        "ок",
        "ага",
        "угу",
        "ладно",
        "хорошо",
        "yes",
        "окей",
        "понял",
        "ясно",
        "отлично",
        "круто",
        "супер",
        "класс",
        "yeah",
        "okay",
        "договорились",
        "конечно",
    ],
)
def test_agreement_passes_through(text: str) -> None:
    """Agreement patterns return None (handled by smart_reply emoji stage)."""
    assert check_pre_gate(text) is None


# ── Category: Strong Disagreement ───────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("отстань", "Понял, не буду мешать."),
        ("хватит", "Понял, не буду мешать."),
        ("стоп", "Понял, не буду мешать."),
        ("прекрати", "Понял, не буду мешать."),
        ("замолчи", "Понял, не буду мешать."),
    ],
)
def test_strong_disagreement(text: str, expected: str) -> None:
    """Strong rejection patterns return disengagement response."""
    assert check_pre_gate(text) == expected


# ── Category: Soft Disagreement ─────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "нет",
        "не",
        "неа",
        "no",
        "nope",
        "не хочу",
        "не надо",
        "не сейчас",
        "потом",
    ],
)
def test_soft_disagreement_passes_through(text: str) -> None:
    """Soft negative patterns return None (let LLM handle context)."""
    assert check_pre_gate(text) is None


# ── Category: Laughter ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("хаха", "😂"),
        ("ахах", "😂"),
        ("лол", "😂"),
        ("ржу", "😂"),
        ("хехе", "😂"),
        ("lol", "😂"),
    ],
)
def test_laughter(text: str, expected: str) -> None:
    """Laughter patterns return emoji response."""
    assert check_pre_gate(text) == expected


# ── Category: Surprise ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ого", "😮"),
        ("вау", "😮"),
        ("wow", "😮"),
        ("ничего себе", "😮"),
    ],
)
def test_surprise(text: str, expected: str) -> None:
    """Surprise patterns return emoji response."""
    assert check_pre_gate(text) == expected


# ── Category: Sympathy ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("жаль", "Понимаю…"),
        ("сочувствую", "Понимаю…"),
        ("бывает", "Понимаю…"),
    ],
)
def test_sympathy(text: str, expected: str) -> None:
    """Sympathy expressions return empathetic response."""
    assert check_pre_gate(text) == expected


# ── Edge Cases ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "",  # Empty string
        "   ",  # Whitespace only
        "какое сегодня число",  # Not a pre-gate pattern
        "расскажи анекдот",  # Needs LLM
        "blabla123unknown",  # Garbage
    ],
)
def test_unknown_text_returns_none(text: str) -> None:
    """Unrecognized text should return None to pass through to LLM."""
    assert check_pre_gate(text) is None


# ── Substring Matching ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("доброго утречка", "Привет! Чем могу помочь?"),
        ("с добрым утром", "Привет! Чем могу помочь?"),
        ("спасибо большое", "Всегда пожалуйста! Рад помочь."),
        ("огромное спасибо", "Всегда пожалуйста! Рад помочь."),
        ("да ладно", "😮"),  # "да ладно" is in surprise category
        ("не может быть", "😮"),
    ],
)
def test_substring_matches(text: str, expected: str) -> None:
    """Multi-word patterns matched via substring search."""
    assert check_pre_gate(text) == expected


# ── Case Insensitivity & Punctuation ────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ДОБРОЕ УТРО", "Привет! Чем могу помочь?"),
        ("ПоКа!", "До связи! Если что — я здесь."),
        ("СпАсИбО...", "Всегда пожалуйста! Рад помочь."),
        ("  Добрый день  ", "Привет! Чем могу помочь?"),
    ],
)
def test_case_insensitivity(text: str, expected: str) -> None:
    """Case-insensitive matching with whitespace and punctuation stripping."""
    assert check_pre_gate(text) == expected


# ── Spam / URL Detection ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "http://example.com",
        "https://spam.net/tracker",
        "заработок http://scam.com",
    ],
)
def test_spam_url_detection(text: str) -> None:
    """URL-only or spammy messages are silently ignored (return None)."""
    assert check_pre_gate(text) is None


# ── Pattern Count Validation ────────────────────────────────────────────


def test_pattern_count() -> None:
    """Verify that we have 100+ patterns loaded across all categories."""
    stats = get_pattern_stats()
    total = sum(stats.values())
    assert total >= 100, (
        f"Expected 100+ patterns, got {total} across {len(stats)} categories"
    )

    # Verify key categories exist
    for cat in (
        "time_greetings",
        "farewells",
        "gratitude",
        "agreement",
        "disagreement",
        "laughter",
        "surprise",
        "sympathy",
    ):
        assert cat in stats, f"Missing category: {cat}"
        assert stats[cat] > 0, f"Empty category: {cat}"


# ── Legacy Compatibility ────────────────────────────────────────────────


def test_legacy_patterns_still_work() -> None:
    """Original pre_gate patterns from the old hardcoded sets still respond."""
    legacy_greetings = ["привет", "здравствуй", "хай", "hello", "hi", "ку"]
    for g in legacy_greetings:
        result = check_pre_gate(g)
        assert result is not None, f"Legacy greeting '{g}' should still match"

    legacy_farewells = ["пока", "до свидания", "bye"]
    for f in legacy_farewells:
        result = check_pre_gate(f)
        assert result is not None, f"Legacy farewell '{f}' should still match"


# ── Performance Smoke Test ──────────────────────────────────────────────


def test_performance_under_1ms() -> None:
    """100 calls should complete quickly even on modest hardware."""
    import time

    samples = [
        "привет",
        "пока",
        "спасибо",
        "хаха",
        "ого",
        "да",
        "нет",
        "отстань",
        "как дела",
        "http://test.com",
        "доброе утро",
        "lol",
        "bye",
        "wow",
        "жаль",
        "unknown text that does not match anything at all 12345",
    ]
    start = time.perf_counter()
    for _ in range(100):
        for s in samples:
            check_pre_gate(s)
    elapsed = time.perf_counter() - start
    # 100 * 16 = 1600 calls. Even on slow hardware this should be < 0.1s total.
    # Per-call target: < 1ms → 1600 calls < 1.6s. We use 0.5s as generous threshold.
    assert elapsed < 0.5, (
        f"Performance check failed: {elapsed:.4f}s for 1600 calls "
        f"({elapsed / 16 * 1000:.2f}μs per call batch of 100)"
    )


# ── Feature Flag: Extended Mode Off ─────────────────────────────────────


def test_extended_mode_disabled(monkeypatch) -> None:
    """When pre_gate_extended=False, only core categories are active."""
    # Reload needed because pre_gate.py imports settings at module level.
    # We test the flag behavior by importing check_pre_gate with monkeypatched settings.

    # Patch the module-level _extended resolution inside check_pre_gate
    # by setting settings.pre_gate_extended = False
    import src.config

    monkeypatch.setattr(src.config.settings, "pre_gate_extended", False)

    # Core categories still work
    assert check_pre_gate("привет") == "Привет! Чем могу помочь?"
    assert check_pre_gate("пока") == "До связи! Если что — я здесь."

    # Extended categories should NOT respond
    assert check_pre_gate("спасибо") is None
    assert check_pre_gate("хаха") is None
    assert check_pre_gate("жаль") is None
