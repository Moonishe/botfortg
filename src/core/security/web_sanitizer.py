"""Sanitize external content before injecting into LLM prompts."""

import unicodedata

# Common Cyrillic homoglyphs that visually mimic Latin letters.
# NFKC does NOT convert between scripts, so we map them manually.
_CYRILLIC_HOMOGLYPHS = str.maketrans(
    {
        "і": "i",  # CYRILLIC SMALL LETTER BYELORUSSIAN-UKRAINIAN I
        "а": "a",  # CYRILLIC SMALL LETTER A
        "е": "e",  # CYRILLIC SMALL LETTER IE
        "к": "k",  # CYRILLIC SMALL LETTER KA — bypasses "override"
        "о": "o",  # CYRILLIC SMALL LETTER O
        "с": "c",  # CYRILLIC SMALL LETTER ES
        "т": "t",  # CYRILLIC SMALL LETTER TE — bypasses "assistant:", "system:", "forget"
        "у": "y",  # CYRILLIC SMALL LETTER U — bypasses "you are now", "you must", "system:"
        "р": "p",  # CYRILLIC SMALL LETTER ER
        "х": "x",  # CYRILLIC SMALL LETTER HA
        "І": "I",  # CYRILLIC CAPITAL LETTER BYELORUSSIAN-UKRAINIAN I
        "А": "A",  # CYRILLIC CAPITAL LETTER A
        "Е": "E",  # CYRILLIC CAPITAL LETTER IE
        "К": "K",  # CYRILLIC CAPITAL LETTER KA
        "О": "O",  # CYRILLIC CAPITAL LETTER O
        "С": "C",  # CYRILLIC CAPITAL LETTER ES
        "Т": "T",  # CYRILLIC CAPITAL LETTER TE
        "У": "Y",  # CYRILLIC CAPITAL LETTER U
        "Р": "P",  # CYRILLIC CAPITAL LETTER ER
        "Х": "X",  # CYRILLIC CAPITAL LETTER HA
    }
)

_INJECTION_BLACKLIST = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "you are now",
    "you are a",
    "system:",
    "assistant:",
    "disregard",
    "forget",
    "you must",
    "new instructions",
    "do not follow",
    "override",
]


def _normalize(text: str) -> str:
    """Normalize (NFKC + homoglyph transliteration) and lowercase text.

    NFKC handles compatibility characters (full-width Latin, ligatures).
    Manual homoglyph map converts Cyrillic lookalikes to Latin equivalents.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_CYRILLIC_HOMOGLYPHS)
    return text.lower()


def sanitize_search_snippet(text: str) -> str:
    """Sanitize a single web search snippet to prevent prompt injection."""
    if not text:
        return ""
    normalized = _normalize(text)
    for phrase in _INJECTION_BLACKLIST:
        if phrase in normalized:
            return "[filtered]"
    return text[:300]


def sanitize_search_result(title: str, snippet: str) -> tuple[str, str]:
    """Sanitize DDG search result (title + snippet) to prevent prompt injection."""
    combined = title + " " + snippet
    normalized = _normalize(combined)
    for phrase in _INJECTION_BLACKLIST:
        if phrase in normalized:
            return "[filtered]", "[filtered]"
    return title[:300], snippet[:300]
