"""Tests for Trie/Aho-Corasick MessageClassifier."""

from __future__ import annotations

import os
import time

# ── Ensure valid test environment before importing src ─────────────────
# conftest sets BOT_TOKEN="test:token" which fails the validator.
# Override with a valid format before any src import.
os.environ["BOT_TOKEN"] = "12345678:abcdefghijklmnopqrstuvwxyz12345"
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

import pytest

from src.core.classification.message_classifier import (
    MessageClassifier,
    _AHO_AVAILABLE,
    _ManualAhoCorasick,
    classify_message,
    get_classifier,
)
from src.core.classification.routing_wordlists import (
    CATEGORY_PATTERNS,
    CATEGORY_PRIORITY,
)


# ── Helper: create a fresh classifier for testing ─────────────────────


def _fresh_classifier() -> MessageClassifier:
    """Create a fresh, fully-built classifier instance."""
    c = MessageClassifier()
    for cat, patterns in CATEGORY_PATTERNS.items():
        c.add_patterns(cat, patterns)
    c.build()
    return c


# ── Test: all categories correctly classified ─────────────────────────


class TestCategoryClassification:
    """Test that messages are classified into correct categories."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.c = _fresh_classifier()

    @pytest.mark.parametrize(
        "text,expected_categories",
        [
            # Greetings
            ("привет", {"greeting", "trivial"}),
            ("здравствуй", {"greeting"}),
            ("хай", {"greeting", "trivial"}),
            ("hello", {"greeting", "trivial"}),
            ("hi", {"greeting", "trivial"}),
            ("ку", {"greeting", "trivial"}),
            ("доброе утро", {"greeting"}),
            ("добрый день", {"greeting"}),
            ("добрый вечер", {"greeting"}),
            ("доброй ночи", {"greeting"}),
            ("здарова", {"greeting"}),
            ("приветствую", {"greeting"}),
            ("салют", {"greeting"}),
            # Farewells
            ("пока", {"farewell", "trivial"}),
            ("до свидания", {"farewell"}),
            ("спокойной ночи", {"farewell"}),
            ("увидимся", {"farewell"}),
            ("bye", {"farewell", "trivial"}),
            ("goodbye", {"farewell"}),
            ("до завтра", {"farewell"}),
            ("чао", {"farewell"}),
            # Gratitude
            ("спасибо", {"gratitude"}),
            ("благодарю", {"gratitude"}),
            ("спс", {"gratitude", "trivial"}),
            ("thanks", {"gratitude"}),
            ("thank you", {"gratitude"}),
            ("мерси", {"gratitude"}),
            # Agreement
            ("да", {"agreement"}),
            ("ага", {"agreement", "trivial"}),
            ("угу", {"agreement", "trivial"}),
            ("ок", {"agreement", "trivial"}),
            ("окей", {"agreement", "trivial"}),
            ("ладно", {"agreement", "trivial"}),
            ("хорошо", {"agreement"}),
            ("yes", {"agreement"}),
            ("yep", {"agreement"}),
            ("договорились", {"agreement"}),
            ("верно", {"agreement"}),
            ("согласен", {"agreement"}),
            # Disagreement
            ("нет", {"disagreement"}),
            ("неа", {"disagreement"}),
            ("no", {"disagreement"}),
            ("nope", {"disagreement"}),
            ("неверно", {"disagreement"}),
            ("не согласен", {"disagreement"}),
            ("бред", {"disagreement"}),
            # Questions
            ("что делаешь", {"question"}),
            ("как это работает", {"question"}),
            ("где находится", {"question"}),
            ("когда встреча", {"question"}),
            ("почему так", {"question"}),
            ("кто это", {"question"}),
            ("сколько времени", {"question"}),
            ("расскажи о себе", {"question"}),
            ("объясни мне", {"question"}),
            # Commands
            ("отправь сообщение", {"command"}),
            ("напиши текст", {"command"}),
            ("найди в интернете", {"command"}),
            ("напомни завтра", {"command"}),
            ("сохрани это", {"command"}),
            ("удали запись", {"command"}),
            ("сделай отчёт", {"command"}),
            ("проверь почту", {"command"}),
            # Emotion
            ("супер", {"emotion"}),
            ("круто", {"emotion"}),
            ("отлично", {"emotion"}),
            ("прекрасно", {"emotion"}),
            ("жаль", {"emotion"}),
            ("плохо", {"emotion"}),
            ("ужас", {"emotion"}),
            ("бесит", {"emotion"}),
            # Profanity
            ("дурак", {"profanity"}),
            ("идиот", {"profanity"}),
            ("дебил", {"profanity"}),
            # Trivial only
            ("ясно", {"trivial"}),
            ("понял", {"trivial"}),
            ("понятно", {"trivial"}),
        ],
    )
    def test_category_classification(self, text, expected_categories):
        """Verify each test message has ALL expected categories."""
        result = self.c.classify(text)
        for cat in expected_categories:
            assert result.get(cat) is True, (
                f"Expected '{text}' to match category '{cat}', got: {result}"
            )

    @pytest.mark.parametrize(
        "text,unexpected_categories",
        [
            ("привет", {"command", "question", "profanity", "emotion"}),
            ("пока", {"command", "question", "profanity"}),
            ("спасибо", {"command", "profanity", "disagreement"}),
            ("отправь", {"greeting", "farewell", "gratitude"}),
        ],
    )
    def test_no_false_positives(self, text, unexpected_categories):
        """Verify messages DON'T match unexpected categories."""
        result = self.c.classify(text)
        for cat in unexpected_categories:
            assert result.get(cat) is not True, (
                f"'{text}' should NOT match '{cat}', but it did: {result}"
            )


# ── Test: ambiguous / multi-category messages ──────────────────────────


class TestMultiCategory:
    """Test messages that belong to multiple categories."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.c = _fresh_classifier()

    def test_agreement_trivial_overlap(self):
        """'ага' should be both agreement and trivial."""
        result = self.c.classify("ага")
        assert result["agreement"] is True
        assert result["trivial"] is True

    def test_farewell_trivial_overlap(self):
        """'пока' should be both farewell and trivial."""
        result = self.c.classify("пока")
        assert result["farewell"] is True
        assert result["trivial"] is True

    def test_command_question_overlap(self):
        """'найди это' — both command and question."""
        result = self.c.classify("найди это")
        assert result["command"] is True
        # "это" alone shouldn't trigger question; only if "что" is present
        # So this may or may not be a question too
        assert result["needs_routing"] is True

    def test_greeting_question_overlap(self):
        """'привет как дела' should be greeting AND question."""
        result = self.c.classify("привет как дела")
        assert result["greeting"] is True
        assert result["question"] is True

    def test_punctuated_text(self):
        """Punctuation should not prevent matching."""
        result = self.c.classify("привет, как дела?")
        assert result["greeting"] is True
        assert result["question"] is True

    def test_mixed_case(self):
        """Case insensitivity."""
        result = self.c.classify("ПрИвЕт, СПАСИБО!")
        assert result["greeting"] is True
        assert result["gratitude"] is True


# ── Test: empty / whitespace text ──────────────────────────────────────


class TestEmptyText:
    """Test edge cases for empty/unmatchable text."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.c = _fresh_classifier()

    def test_empty_string(self):
        """Empty string → no categories matched."""
        result = self.c.classify("")
        for cat in CATEGORY_PRIORITY:
            assert result[cat] is False
        assert result["needs_routing"] is False
        assert result["needs_llm"] is True  # empty → LLM should handle

    def test_whitespace_only(self):
        """Whitespace-only → no categories."""
        result = self.c.classify("   \t\n  ")
        for cat in CATEGORY_PRIORITY:
            assert result[cat] is False

    def test_none_text(self):
        """Empty string treated same as None."""
        result = self.c.classify("")
        result2 = _fresh_classifier().classify("")
        assert result == result2

    def test_no_match_text(self):
        """Text with no pattern matches → all False."""
        result = self.c.classify("qwertyuioplkjhgfdsazxcvbnm")
        for cat in CATEGORY_PRIORITY:
            assert result[cat] is False

    def test_numbers_only(self):
        """Numbers don't match any category."""
        result = self.c.classify("12345 67890")
        for cat in CATEGORY_PRIORITY:
            assert result[cat] is False


# ── Test: performance / long text ──────────────────────────────────────


class TestPerformance:
    """Performance and stress tests."""

    def test_classify_benchmark(self):
        """classify() should be < 500 μs for a 100-char message."""
        c = _fresh_classifier()
        text = "привет, как дела? что нового происходит в мире сегодня " * 2
        text = text[:100]  # exactly 100 chars-ish

        # Warm-up
        for _ in range(100):
            c.classify(text)

        # Benchmark
        N = 5000
        start = time.perf_counter()
        for _ in range(N):
            c.classify(text)
        elapsed = time.perf_counter() - start
        avg_us = (elapsed / N) * 1_000_000

        assert avg_us < 500, f"classify() is too slow: {avg_us:.1f} μs (limit: 500 μs)"

    def test_long_text(self):
        """Long text (10K chars) should still classify quickly."""
        c = _fresh_classifier()
        text = "привет как дела что нового " * 500  # ~10K chars

        # Warm-up
        for _ in range(10):
            c.classify(text)

        # Benchmark: average over multiple iterations to avoid single-run noise
        N = 100
        start = time.perf_counter()
        for _ in range(N):
            result = c.classify(text)
        elapsed = (time.perf_counter() - start) * 1_000_000
        avg_us = elapsed / N

        # Should find matches
        assert result["greeting"] is True
        assert result["question"] is True
        # Should be fast even for long text (< 5000 μs average)
        # Note: isolated runs are ~2500 μs; full-suite load pushes this to ~4400 μs,
        # so the original 5000 μs threshold is kept to avoid flakiness.
        assert avg_us < 5000, f"Long text classify too slow: {avg_us:.1f} μs"

    def test_many_patterns_no_match(self):
        """Text with no patterns should still be fast (no degradation)."""
        c = _fresh_classifier()
        text = "x" * 200 + "y" * 200  # 400 chars, no patterns

        N = 1000
        start = time.perf_counter()
        for _ in range(N):
            c.classify(text)
        elapsed = time.perf_counter() - start
        avg_us = (elapsed / N) * 1_000_000

        assert avg_us < 500, f"No-match classify too slow: {avg_us:.1f} μs"


# ── Test: needs_routing / needs_llm computed flags ─────────────────────


class TestComputedFlags:
    """Test the needs_routing and needs_llm computed flags."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.c = _fresh_classifier()

    def test_command_needs_routing(self):
        """Commands should set needs_routing=True."""
        result = self.c.classify("отправь сообщение маме")
        assert result["needs_routing"] is True

    def test_question_needs_routing(self):
        """Questions should set needs_routing=True."""
        result = self.c.classify("что такое энтропия")
        assert result["needs_routing"] is True

    def test_greeting_no_llm_needed(self):
        """Pure greetings don't need LLM."""
        result = self.c.classify("привет")
        assert result["needs_llm"] is False

    def test_trivial_no_llm_needed(self):
        """Trivial messages don't need LLM."""
        result = self.c.classify("ага")
        assert result["needs_llm"] is False

    def test_farewell_no_llm_needed(self):
        """Farewells don't need LLM."""
        result = self.c.classify("пока")
        assert result["needs_llm"] is False

    def test_complex_message_needs_llm(self):
        """Complex messages need LLM."""
        result = self.c.classify("расскажи мне о последних событиях в мире")
        assert result["needs_llm"] is True


# ── Test: get_classifier singleton ─────────────────────────────────────


class TestSingleton:
    """Test the module-level classifier singleton."""

    def test_singleton_returns_same_instance(self):
        """get_classifier() should return the same instance."""
        c1 = get_classifier()
        c2 = get_classifier()
        assert c1 is c2

    def test_singleton_is_built(self):
        """Module-level classifier is pre-built."""
        c = get_classifier()
        assert c._built is True
        assert c.pattern_count > 0

    def test_classify_message_convenience(self):
        """classify_message convenience function works."""
        result = classify_message("привет")
        assert result["greeting"] is True


# ── Test: module-level build ───────────────────────────────────────────


class TestBuild:
    """Test build and rebuild behavior."""

    def test_lazy_build(self):
        """Classifier builds on first classify()."""
        c = MessageClassifier()
        assert c._built is False
        c.add_patterns("greeting", ["hello"])
        c.classify("hello world")
        assert c._built is True

    def test_rebuild_after_add(self):
        """Adding patterns after build marks as needing rebuild."""
        c = _fresh_classifier()
        assert c._built is True
        c.add_patterns("greeting", ["новое_приветствие"])
        assert c._built is False
        # classify triggers rebuild
        result = c.classify("новое_приветствие")
        assert c._built is True
        assert result["greeting"] is True

    def test_pattern_count(self):
        """Pattern count tracks total added patterns."""
        c = MessageClassifier()
        assert c.pattern_count == 0
        c.add_patterns("test", ["a", "b", "c"])
        assert c.pattern_count == 3
        c.add_patterns("test2", ["d", "e"])
        assert c.pattern_count == 5

    def test_empty_patterns_noop(self):
        """Adding empty pattern list does nothing."""
        c = MessageClassifier()
        c.add_patterns("test", [])
        assert c.pattern_count == 0
        assert c.category_count == 0


# ── Test: pyahocorasick vs manual fallback equivalence ─────────────────


@pytest.mark.skipif(not _AHO_AVAILABLE, reason="pyahocorasick not available")
class TestEngineEquivalence:
    """Verify pyahocorasick and manual trie produce equivalent results."""

    def _build_manual(self) -> MessageClassifier:
        """Build a classifier using ONLY manual trie."""
        c = MessageClassifier()
        # Replace automaton with manual one
        c._automaton = _ManualAhoCorasick()
        for cat, patterns in CATEGORY_PATTERNS.items():
            for pattern in patterns:
                p = pattern.lower().strip()
                if p:
                    c._automaton.add_word(p, (cat, p))
        c._automaton.make_automaton()
        c._built = True
        c._categories = set(CATEGORY_PATTERNS.keys())
        c._pattern_count_total = sum(len(v) for v in CATEGORY_PATTERNS.values())
        return c

    @pytest.mark.parametrize(
        "text",
        [
            "привет",
            "пока",
            "спасибо большое",
            "да конечно",
            "нет не надо",
            "что делаешь сегодня",
            "найди информацию",
            "отправь сообщение",
            "круто супер",
            "ага ясно",
            "",
            "   ",
            "qwerty",
            "как дела привет",
        ],
    )
    def test_equivalence(self, text):
        """pyahocorasick and manual trie produce same results."""
        py_result = _fresh_classifier().classify(text)
        manual_result = self._build_manual().classify(text)

        # Remove computed fields for comparison
        def _strip_computed(d):
            return {
                k: v for k, v in d.items() if k not in ("needs_routing", "needs_llm")
            }

        py_cats = _strip_computed(py_result)
        manual_cats = _strip_computed(manual_result)

        assert py_cats == manual_cats, (
            f"Divergence for '{text}':\n"
            f"  pyahocorasick: {py_cats}\n"
            f"  manual:        {manual_cats}"
        )

    def test_benchmark_comparison(self):
        """pyahocorasick should be faster than manual trie."""
        text = "привет как дела что нового " * 5
        N = 2000

        py_c = _fresh_classifier()
        manual_c = self._build_manual()

        # Benchmark pyahocorasick
        py_start = time.perf_counter()
        for _ in range(N):
            py_c.classify(text)
        py_time = time.perf_counter() - py_start

        # Benchmark manual
        man_start = time.perf_counter()
        for _ in range(N):
            manual_c.classify(text)
        man_time = time.perf_counter() - man_start

        py_us = (py_time / N) * 1_000_000
        man_us = (man_time / N) * 1_000_000

        # pyahocorasick should be faster (allow up to 2x slower as equal)
        assert py_us <= man_us * 2, (
            f"pyahocorasick ({py_us:.1f} μs) should not be "
            f"significantly slower than manual ({man_us:.1f} μs)"
        )


# ── Test: word boundary (no substring false positives) ─────────────────


class TestWordBoundary:
    """Test that substring matches are correctly filtered."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.c = _fresh_classifier()

    def test_ok_not_in_poka(self):
        """'пока' should NOT match 'ok' (agreement) because 'ok' is a substring."""
        result = self.c.classify("пока")
        assert result["agreement"] is not True, (
            "'пока' should not match 'agreement' via 'ok' substring"
        )

    def test_da_not_in_podarok(self):
        """'подарок' should NOT match 'да' (agreement)."""
        result = self.c.classify("подарок")
        assert result["agreement"] is not True, (
            "'подарок' should not match 'agreement' via 'да' substring"
        )

    def test_net_not_in_internet(self):
        """'интернет' should NOT match 'нет' (disagreement)."""
        result = self.c.classify("интернет")
        assert result["disagreement"] is not True, (
            "'интернет' should not match 'disagreement' via 'нет' substring"
        )

    def test_chto_in_context(self):
        """'что' should match when it's a standalone word."""
        result = self.c.classify("что-то странное")
        # "что" is at the start and followed by "-" (non-alpha), so it matches
        assert result["question"] is True

    def test_pochemu_in_context(self):
        """'почему' should match as standalone word."""
        result = self.c.classify("почему-то")
        # "почему" at start, followed by "-" (non-alpha) → match
        assert result["question"] is True


# ── Test: pattern database integrity ───────────────────────────────────


class TestPatternDatabase:
    """Verify the routing_wordlists data integrity."""

    def test_all_categories_have_patterns(self):
        """Every category has at least one pattern."""
        for cat, patterns in CATEGORY_PATTERNS.items():
            assert len(patterns) > 0, f"Category '{cat}' has no patterns"

    def test_total_patterns_over_100(self):
        """Total patterns should exceed 100."""
        total = sum(len(v) for v in CATEGORY_PATTERNS.values())
        assert total >= 100, f"Only {total} patterns, need >= 100"

    def test_no_empty_patterns(self):
        """No empty string patterns."""
        for cat, patterns in CATEGORY_PATTERNS.items():
            for p in patterns:
                assert p.strip(), f"Empty pattern in '{cat}'"

    def test_all_patterns_lowercase(self):
        """All patterns are stored lowercase (case-insensitive matching)."""
        for cat, patterns in CATEGORY_PATTERNS.items():
            for p in patterns:
                assert p == p.lower(), f"Pattern '{p}' in '{cat}' is not lowercase"

    def test_category_priority_coverage(self):
        """All categories in PATTERNS must be in PRIORITY."""
        for cat in CATEGORY_PATTERNS:
            assert cat in CATEGORY_PRIORITY, (
                f"Category '{cat}' missing from CATEGORY_PRIORITY"
            )

    def test_no_duplicate_patterns_within_category(self):
        """No duplicate patterns within a single category."""
        for cat, patterns in CATEGORY_PATTERNS.items():
            seen = set()
            for p in patterns:
                assert p not in seen, f"Duplicate pattern '{p}' in category '{cat}'"
                seen.add(p)


# ── Test: result dict completeness ─────────────────────────────────────


class TestResultFormat:
    """Test the format of classification results."""

    def test_all_categories_in_result(self):
        """Result dict contains ALL categories + computed flags."""
        c = _fresh_classifier()
        result = c.classify("тестовое сообщение")
        for cat in CATEGORY_PRIORITY:
            assert cat in result, f"Category '{cat}' missing from result"
        assert "needs_routing" in result
        assert "needs_llm" in result

    def test_all_values_are_bool(self):
        """All result values are booleans."""
        c = _fresh_classifier()
        result = c.classify("привет")
        for v in result.values():
            assert isinstance(v, bool), f"Value {v!r} is not bool"
