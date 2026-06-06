"""Single-pass multi-category message classifier using Aho-Corasick automaton.

Classifies a message into: greeting, gratitude, question, command,
farewell, agreement, disagreement, emotion, profanity, trivial.

O(n + m) where n = message length, m = number of matches found.

Uses pyahocorasick if available; falls back to manual Trie + BFS failure links.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Final

from .routing_wordlists import CATEGORY_PATTERNS, CATEGORY_PRIORITY

logger = logging.getLogger(__name__)

# ── pyahocorasick availability ────────────────────────────────────────
try:
    import ahocorasick as _aho

    _AHO_AVAILABLE = True
    _aho_Automaton = _aho.Automaton  # type: ignore[attr-defined]
except ImportError:
    _AHO_AVAILABLE = False
    _aho = None  # type: ignore[assignment]
    logger.info("pyahocorasick not available; using manual Trie fallback")


# ── Manual Trie + BFS failure links (fallback) ────────────────────────


class _TrieNode:
    __slots__ = ("children", "fail", "output")

    def __init__(self) -> None:
        self.children: dict[str, _TrieNode] = {}
        self.fail: _TrieNode | None = None
        self.output: list[tuple[str, str]] = []  # (category, pattern)


class _ManualAhoCorasick:
    """Manual Aho-Corasick automaton as pyahocorasick fallback.

    Constructs the trie, then builds failure links via BFS.
    Matches are found via the standard Aho-Corasick traversal.
    """

    def __init__(self) -> None:
        self._root = _TrieNode()
        self._built = False
        self._pattern_count = 0

    def add_word(self, pattern: str, value: tuple[str, str]) -> None:
        """Add a pattern with associated (category, pattern) value."""
        node = self._root
        for ch in pattern:
            if ch not in node.children:
                node.children[ch] = _TrieNode()
            node = node.children[ch]
        node.output.append(value)
        self._pattern_count += 1

    def make_automaton(self) -> None:
        """Build failure links via BFS. Rebuilds from scratch if called again."""
        if not self._built:
            self._build_bfs()
            self._built = True

    def _build_bfs(self) -> None:
        """Internal BFS failure-link construction."""
        from collections import deque

        q: deque[_TrieNode] = deque()

        # Root's children fail to root
        for child in self._root.children.values():
            child.fail = self._root
            q.append(child)

        # Reset root output (patterns accumulated via root node)
        self._root.output = []

        # BFS
        while q:
            current = q.popleft()
            for ch, nxt in current.children.items():
                q.append(nxt)
                # Follow failure links to find the longest proper suffix
                f = current.fail
                while f is not None and ch not in f.children:
                    f = f.fail
                if f is None:
                    nxt.fail = self._root
                else:
                    nxt.fail = f.children[ch]
                # Merge output from failure node
                if nxt.fail is not None:
                    nxt.output.extend(nxt.fail.output)

    def iter(self, text: str) -> list[tuple[int, tuple[str, str]]]:
        """Iterate over (end_index, (category, pattern)) matches."""
        if not self._built:
            self.make_automaton()
        result: list[tuple[int, tuple[str, str]]] = []
        text_lower = text.lower()
        node = self._root
        for i, ch in enumerate(text_lower):
            while node is not self._root and ch not in node.children:
                node = node.fail  # type: ignore[assignment]
            if ch in node.children:
                node = node.children[ch]
            else:
                node = self._root
            for cat_pat in node.output:
                result.append((i, cat_pat))
        return result

    def __contains__(self, pattern: str) -> bool:
        """Check if pattern exists in the automaton."""
        return self.exists(pattern)

    def exists(self, pattern: str) -> bool:
        """Check if pattern exists in the automaton."""
        return self.find_first(pattern) is not None

    def find_first(self, text: str) -> tuple[int, tuple[str, str]] | None:
        """Return first match or None."""
        items = self.iter(text)
        return items[0] if items else None

    @property
    def pattern_count(self) -> int:
        return self._pattern_count


# ── PyAhoCorasick wrapper ─────────────────────────────────────────────


class _PyAhoCorasickAdapter:
    """Thin adapter wrapping pyahocorasick.Automaton.

    Supports multi-category patterns (same pattern string in different
    categories) by accumulating values as lists.
    """

    def __init__(self) -> None:
        self._auto = _aho_Automaton()
        self._built = False
        self._pattern_count = 0
        # Track per-pattern categories for dedup/multi-value support
        self._pattern_multimap: dict[str, set[tuple[str, str]]] = {}

    def add_word(self, pattern: str, value: tuple[str, str]) -> None:
        key = pattern
        if key in self._pattern_multimap:
            # Already exists — accumulate
            self._pattern_multimap[key].add(value)
        else:
            self._pattern_multimap[key] = {value}
        self._pattern_count += 1

    def make_automaton(self) -> None:
        if not self._built:
            # Build fresh automaton — clear and re-add all accumulated patterns
            self._auto = _aho_Automaton()
            for key, values in self._pattern_multimap.items():
                vlist = list(values)
                self._auto.add_word(key, vlist)
            self._auto.make_automaton()
            self._built = True

    def iter(self, text: str) -> list[tuple[int, tuple[str, str]]]:
        if not self._built:
            self.make_automaton()
        result: list[tuple[int, tuple[str, str]]] = []
        for end_idx, val in self._auto.iter(text.lower()):
            if isinstance(val, list):
                for v in val:
                    result.append((end_idx, v))
            else:
                result.append((end_idx, val))
        return result

    def __contains__(self, pattern: str) -> bool:
        if not self._built:
            self.make_automaton()
        return pattern.lower() in self._auto

    def exists(self, pattern: str) -> bool:
        return pattern.lower() in self

    def find_first(self, text: str) -> tuple[int, tuple[str, str]] | None:
        if not self._built:
            self.make_automaton()
        try:
            item = next(self._auto.iter(text.lower()))
            return item
        except StopIteration:
            return None

    @property
    def pattern_count(self) -> int:
        return self._pattern_count


# ── Automaton factory ─────────────────────────────────────────────────


def _create_automaton():
    """Create the appropriate automaton based on availability."""
    if _AHO_AVAILABLE:
        return _PyAhoCorasickAdapter()
    else:
        return _ManualAhoCorasick()


# ── MessageClassifier ─────────────────────────────────────────────────

# Categories that indicate the message needs routing to a specific handler
_ROUTING_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "command",
        "question",
    }
)

# Categories that are purely trivial/acknowledgment — no LLM needed
_TRIVIAL_GATE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "greeting",
        "farewell",
        "trivial",
    }
)


class MessageClassifier:
    """Single-pass multi-category message classifier.

    Classifies a message into: greeting, gratitude, question, command,
    farewell, agreement, disagreement, emotion, profanity, trivial, needs_routing.

    O(n + m) where n = message length, m = number of matches found.

    Usage:
        classifier = MessageClassifier()
        result = classifier.classify("привет, как дела?")
        # result = {"greeting": True, "question": True, "needs_routing": False, ...}
    """

    def __init__(self) -> None:
        self._automaton: _PyAhoCorasickAdapter | _ManualAhoCorasick = (
            _create_automaton()
        )
        self._built = False
        # Per-category match tracking
        self._categories: set[str] = set()
        self._pattern_count_total = 0

    # ── Public API ────────────────────────────────────────────────────

    def classify(self, text: str) -> dict[str, bool]:
        """Classify a message into categories.

        Args:
            text: Raw message text.

        Returns:
            Dict mapping category → matched (bool). Always contains
            all known categories + 'needs_routing' computed flag.
        """
        # Lazy-build on first use (after add_patterns calls in __init__ pattern)
        self._ensure_built()

        if not text or not text.strip():
            return self._empty_result()

        text_lower = text.lower().strip()
        n = len(text_lower)

        matches = self._automaton.iter(text)
        if not matches:
            return self._empty_result()

        # Collect unique categories matched, with word-boundary check
        matched_cats: set[str] = set()
        for end_idx, val in matches:
            # val may be a list of (category, pattern) tuples
            items: list[tuple[str, str]] = val if isinstance(val, list) else [val]
            for category, pattern in items:
                pat_len = len(pattern)
                start_idx = end_idx - pat_len + 1
                if start_idx < 0:
                    continue
                # Word-boundary check: avoid substring matches like "ок" in "пока"
                is_start_ok = start_idx == 0 or not text_lower[start_idx - 1].isalpha()
                is_end_ok = end_idx == n - 1 or not text_lower[end_idx + 1].isalpha()
                if is_start_ok and is_end_ok:
                    matched_cats.add(category)

        return self._build_result(matched_cats)

    def add_patterns(self, category: str, patterns: list[str]) -> None:
        """Add patterns for a category. Automaton will be rebuilt on next classify().

        Args:
            category: Category name (must be in CATEGORY_PRIORITY).
            patterns: List of pattern strings for this category.
        """
        if not patterns:
            return
        for pattern in patterns:
            p = pattern.lower().strip()
            if not p:
                continue
            value = (category, p)
            self._automaton.add_word(p, value)
            self._pattern_count_total += 1
        self._categories.add(category)
        # Reset both classifier and adapter build flags so patterns
        # are flushed into the automaton on next classify()
        self._built = False
        self._automaton._built = False  # force adapter to rebuild

    def build(self) -> None:
        """Build the Aho-Corasick automaton from all registered patterns."""
        self._automaton.make_automaton()
        self._built = True
        logger.debug(
            "Classifier automaton built: %d patterns across %d categories (engine: %s)",
            self._pattern_count_total,
            len(self._categories),
            "pyahocorasick" if _AHO_AVAILABLE else "manual-trie",
        )

    # ── Properties ────────────────────────────────────────────────────

    @property
    def pattern_count(self) -> int:
        return self._pattern_count_total

    @property
    def category_count(self) -> int:
        return len(self._categories)

    @property
    def engine_name(self) -> str:
        return "pyahocorasick" if _AHO_AVAILABLE else "manual-trie"

    # ── Internal ──────────────────────────────────────────────────────

    def _ensure_built(self) -> None:
        if not self._built:
            self.build()

    def _build_result(self, matched_cats: set[str]) -> dict[str, bool]:
        """Build result dict with all categories + computed flags."""
        result: dict[str, bool] = {}
        for category in sorted(CATEGORY_PRIORITY.keys()):
            result[category] = category in matched_cats

        # Computed flags
        result["needs_routing"] = bool(matched_cats & _ROUTING_CATEGORIES)
        result["needs_llm"] = not bool(matched_cats & _TRIVIAL_GATE_CATEGORIES)

        return result

    @staticmethod
    def _empty_result() -> dict[str, bool]:
        """Return result for empty/unmatched text."""
        result: dict[str, bool] = {}
        for category in sorted(CATEGORY_PRIORITY.keys()):
            result[category] = False
        result["needs_routing"] = False
        result["needs_llm"] = True  # empty → LLM should handle
        return result


# ── Module-level singleton, built at import time ──────────────────────


_module_classifier = MessageClassifier()

# Register all patterns from routing_wordlists
for _cat, _patterns in CATEGORY_PATTERNS.items():
    _module_classifier.add_patterns(_cat, _patterns)

# Build once at import
_module_classifier.build()


def get_classifier() -> MessageClassifier:
    """Return the module-level classifier singleton (pre-built)."""
    return _module_classifier


def classify_message(text: str) -> dict[str, bool]:
    """Convenience function: classify a message using the module-level classifier."""
    return _module_classifier.classify(text)
