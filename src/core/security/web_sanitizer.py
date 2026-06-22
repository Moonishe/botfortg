"""Sanitize external content before injecting into LLM prompts."""

import re
import unicodedata

# Common Cyrillic + Greek homoglyphs that visually mimic Latin letters.
# NFKC does NOT convert between scripts, so we map them manually.
_CYRILLIC_HOMOGLYPHS = str.maketrans(
    {
        # Cyrillic
        "і": "i",  # CYRILLIC SMALL LETTER BYELORUSSIAN-UKRAINIAN I
        "а": "a",  # CYRILLIC SMALL LETTER A
        "е": "e",  # CYRILLIC SMALL LETTER IE
        "к": "k",  # CYRILLIC SMALL LETTER KA — bypasses "override"
        "о": "o",  # CYRILLIC SMALL LETTER O
        "с": "c",  # CYRILLIC SMALL LETTER ES
        # CYRILLIC SMALL LETTER TE — bypasses "assistant:", "system:", "forget"
        "т": "t",
        # CYRILLIC SMALL LETTER U — bypasses "you are now", "you must", "system:"
        "у": "y",
        "р": "p",  # CYRILLIC SMALL LETTER ER
        "х": "x",  # CYRILLIC SMALL LETTER HA
        "в": "b",  # CYRILLIC SMALL LETTER VE
        "м": "m",  # CYRILLIC SMALL LETTER EM
        "н": "n",  # CYRILLIC SMALL LETTER EN
        "ѕ": "s",  # CYRILLIC SMALL LETTER DZE
        "ј": "j",  # CYRILLIC SMALL LETTER JE
        "һ": "h",  # CYRILLIC SMALL LETTER SHHA
        "қ": "k",  # CYRILLIC SMALL LETTER QA
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
        "В": "B",  # CYRILLIC CAPITAL LETTER VE
        "М": "M",  # CYRILLIC CAPITAL LETTER EM
        "Н": "H",  # CYRILLIC CAPITAL LETTER EN
        "Ѕ": "S",  # CYRILLIC CAPITAL LETTER DZE
        "Ј": "J",  # CYRILLIC CAPITAL LETTER JE
        "Һ": "H",  # CYRILLIC CAPITAL LETTER SHHA
        "Қ": "K",  # CYRILLIC CAPITAL LETTER QA
        # Greek lowercase
        "\u03bf": "o",  # ο — GREEK SMALL LETTER OMICRON
        "\u03bd": "v",  # ν — GREEK SMALL LETTER NU
        "\u03b5": "e",  # ε — GREEK SMALL LETTER EPSILON
        "\u03b1": "a",  # α — GREEK SMALL LETTER ALPHA
        "\u03c1": "p",  # ρ — GREEK SMALL LETTER RHO
        "\u03c4": "t",  # τ — GREEK SMALL LETTER TAU
        "\u03ba": "k",  # κ — GREEK SMALL LETTER KAPPA
        "\u03c7": "x",  # χ — GREEK SMALL LETTER CHI
        "\u03c5": "u",  # υ — GREEK SMALL LETTER UPSILON (visual 'u')
        "\u03b6": "z",  # ζ — GREEK SMALL LETTER ZETA
        "\u03b7": "n",  # η — GREEK SMALL LETTER ETA
        "\u03b9": "i",  # ι — GREEK SMALL LETTER IOTA
        "\u03b3": "y",  # γ GREEK SMALL LETTER GAMMA → y
        "\u03bc": "u",  # μ GREEK SMALL LETTER MU → u
        # Greek uppercase
        "\u039f": "O",  # Ο — GREEK CAPITAL LETTER OMICRON
        "\u039d": "N",  # Ν — GREEK CAPITAL LETTER NU
        "\u0395": "E",  # Ε — GREEK CAPITAL LETTER EPSILON
        "\u0391": "A",  # Α — GREEK CAPITAL LETTER ALPHA
        "\u03a1": "P",  # Ρ — GREEK CAPITAL LETTER RHO
        "\u03a4": "T",  # Τ — GREEK CAPITAL LETTER TAU
        "\u039a": "K",  # Κ — GREEK CAPITAL LETTER KAPPA
        "\u03a7": "X",  # Χ — GREEK CAPITAL LETTER CHI
        "\u03a5": "Y",  # Υ — GREEK CAPITAL LETTER UPSILON
        "\u039c": "M",  # Μ — GREEK CAPITAL LETTER MU
        "\u0393": "G",  # Γ GREEK CAPITAL LETTER GAMMA → G
        "\u0397": "H",  # Η — GREEK CAPITAL LETTER ETA
        "\u0392": "B",  # Β — GREEK CAPITAL LETTER BETA
        "\u0399": "I",  # Ι — GREEK CAPITAL LETTER IOTA
        "\u0396": "Z",  # Ζ — GREEK CAPITAL LETTER ZETA
    }
)


# Zero-width and invisible characters to strip entirely.
# U+200B ZWSP, U+200C ZWNJ, U+200D ZWJ, U+200E LRM, U+200F RLM,
# U+FEFF BOM/ZWNBS, U+2060 WJ (word joiner).
_ZERO_WIDTH_CHARS = str.maketrans(
    dict.fromkeys(
        "\u200b\u200c\u200d\u200e\u200f\ufeff\u2060"
        "\u2061\u2062\u2063\u2064"          # invisible operators
        "\u202a\u202b\u202c\u202d\u202e"    # BiDi overrides
        "\u2066\u2067\u2068\u2069",          # directional isolates
        "",
    )
)

# Tags block (U+E0001-U+E007F) — invisible language tagging characters.
_TAGS_BLOCK = re.compile(r"[\U000E0001-\U000E007F]")

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

    NFKC handles compatibility characters (full-width Latin, ligatures,
    mathematical alphanumerics). Manual homoglyph map converts Cyrillic + Greek
    lookalikes to Latin equivalents. Zero-width characters are stripped.
    """
    text = text.translate(_ZERO_WIDTH_CHARS)
    text = _TAGS_BLOCK.sub("", text)
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
