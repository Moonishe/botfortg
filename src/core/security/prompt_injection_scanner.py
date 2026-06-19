"""Prompt injection scanner — protects context files from malicious content.

Multi-layer defence:
1. Direct pattern matching (denylist of known injection patterns)
2. Encoded injection detection (base64, URL, leetspeak, hex, unicode, ROT13)
3. Recursive re-scan: decoded output re-scanned through ALL decoders
4. Heuristic suspicion score: instruction-like structures
5. Homoglyph + combining character detection
6. Chat template marker detection (system prompt boundary injection)
"""

from __future__ import annotations
import html
import base64
import codecs
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    blocked: bool
    category: str = ""
    match: str = ""
    file: str = ""
    message: str = ""


_PATTERNS: dict[str, list[str]] = {
    "instruction_override": [
        r"ignore\s+(all\s+)?(previous|prior)\s+instructions",
        r"disregard\s+(your\s+)?(rules|instructions|guidelines)",
        r"forget\s+(everything|all\s+previous)",
        r"you\s+are\s+(not\s+)?(required\s+to|obligated\s+to)",
        r"override\s+(system\s+)?prompt",
        r"новые\s+инструкции",
        r"игнорируй\s+(все\s+)?(предыдущие|прошлые)\s+(правила|инструкции)",
        r"забудь\s+(всё|все\s+предыдущее)",
        r"теперь\s+ты\s+(должен|обязан)",
    ],
    "exfiltration": [
        r"curl\s+.*\$\{?\w*(API_KEY|TOKEN|SECRET|PASSWORD)\}?",
        r"wget\s+.*\$\{?\w*(API_KEY|TOKEN)\}?",
        r"cat\s+\$HOME/\.\w+",
        r"(send|post|upload).*(secret|key|token|credential)",
        r"отправь\s+(мне\s+)?(токен|ключ|пароль|секрет)",
        r"покажи\s+(\.env|config|настройки)",
    ],
    "hidden_content": [
        r"<!--[\s\S]*?(?:ignore|override|instructions|инструкци)[\s\S]*?-->",
        r'<div\s+style=["\']display:\s*none["\']>[\s\S]*?</div>',
        r'<span\s+style=["\']visibility:\s*hidden["\']>[\s\S]*?</span>',
    ],
    "markdown_fence_injection": [
        r"```system",
        r"<!--",
        r"<\|im_start\|>",
    ],
    "unicode_bypass": [
        r"[\u200B\u200C\u200D\uFEFF\u2060\u2061\u2062\u2063\u2064]",
        r"[\u202A\u202B\u202C\u202D\u202E\u2066\u2067\u2068\u2069]",
        r"[\U000E0001-\U000E007F]",
    ],
}

# Cyrillic -> Latin transliteration for homoglyph detection
_CYR_TO_LAT: dict[str, str] = {
    "\u0430": "a",
    "\u0435": "e",
    "\u043e": "o",
    "\u0440": "p",
    "\u0441": "c",
    "\u0443": "y",
    "\u0445": "x",
    "\u0456": "i",
    "\u0455": "s",
    "\u0458": "j",
    "\u04bb": "h",
    "\u049b": "k",
    "\u0410": "A",
    "\u0412": "B",
    "\u0415": "E",
    "\u041a": "K",
    "\u041c": "M",
    "\u041d": "H",
    "\u041e": "O",
    "\u0420": "P",
    "\u0421": "C",
    "\u0422": "T",
    "\u0423": "Y",
    "\u0425": "X",
}

_INJECTION_AFTER_NORMALIZE = [
    r"ignore\s+(all\s+)?(previous|prior)\s+instructions",
    r"disregard\s+(your\s+)?(rules|instructions|guidelines)",
    r"forget\s+(everything|all\s+previous)",
    r"override\s+(system\s+)?prompt",
]

_COMBINING_RANGE = re.compile(r"[\u0300-\u036F\uFE00-\uFE0F\u1DC0-\u1DFF]{3,}")
_BASE64_SUSPECT = re.compile(r"[A-Za-z0-9+/]{30,}={0,2}")

_LEET_MAP: dict[str, str] = {
    "0": "o",
    "1": "i",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    "@": "a",
    "$": "s",
}

# Named constants for decode limits
_MAX_B64_CHUNKS = 20
_MAX_B64_CHUNK_CHARS = 2048
_MAX_B64_TOTAL_CHARS = 10000
_MAX_B64_RAW_CHUNK = 4096
_MAX_DECODED_CHARS = 5000

# Recursive re-scan depth limit (prevents DoS from layered encoding)
_MAX_SCAN_DEPTH = 3

# Heuristic suspicion score threshold
_SUSPICION_THRESHOLD = 5

# Chat template markers (system prompt boundary injection)
_CHAT_TEMPLATE_MARKERS = re.compile(
    r"<\|im_start\|>|<\|im_end\|>|\[/INST\]|\[INST\]|<<SYS>>|<SYS>|"
    r"<\|system\|>|<\|assistant\|>|<\|user\|>",
    re.IGNORECASE,
)

# Role injection markers at start of line
_ROLE_INJECTION = re.compile(
    r"^\s*(system|assistant|user)\s*:",
    re.IGNORECASE | re.MULTILINE,
)

# Instruction-like imperative patterns (heuristic, not denylist)
_IMPERATIVE_PATTERNS = re.compile(
    r"\b(you\s+(?:must|should|need\s+to|have\s+to|are\s+now)|"
    r"always\s+remember|never\s+(?:forget|reveal|share)|"
    r"act\s+as|pretend\s+(?:to\s+be|you\s+are)|"
    r"from\s+now\s+on|starting\s+now|"
    r"ты\s+(?:теперь|обязан|должен)|"
    r"всегда\s+помни|никогда\s+не\s+(?:забывай|раскрывай))\b",
    re.IGNORECASE,
)

# Suspicious system-file references
_SYSTEM_FILE_REFS = re.compile(
    r"(\/etc\/passwd|\/etc\/shadow|\.env\b|config\.py\b|settings\.py\b|"
    r"\/proc\/self|\/var\/log|id_rsa|\.ssh\/)",
    re.IGNORECASE,
)

_HEX_ESCAPE = re.compile(r"\\x[0-9a-fA-F]{2}")
_UNICODE_ESCAPE = re.compile(r"\\u[0-9a-fA-F]{4}")
_ALL_ESCAPES = re.compile(r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4})")


def _normalize_leet(text: str) -> str:
    """Replace common leetspeak substitutions."""
    return "".join(_LEET_MAP.get(c, c) for c in text)


def _try_decode_hex(content: str) -> str | None:
    """Decode \\xNN hex escapes, preserving non-escape context."""
    try:
        decoded = _HEX_ESCAPE.sub(
            lambda m: chr(int(m.group()[2:], 16)),
            content,
            count=_MAX_DECODED_CHARS,
        )
        return decoded if decoded != content else None
    except (ValueError, OverflowError):
        return None


def _try_decode_unicode(content: str) -> str | None:
    """Decode \\uNNNN unicode escapes, preserving non-escape context."""
    try:
        decoded = _UNICODE_ESCAPE.sub(
            lambda m: chr(int(m.group()[2:], 16)),
            content,
            count=_MAX_DECODED_CHARS,
        )
        return decoded if decoded != content else None
    except (ValueError, OverflowError):
        return None


def _try_decode_all_escapes(content: str) -> str | None:
    """Decode BOTH \\xNN and \\uNNNN in a single pass (mixed-encoding bypass)."""
    try:
        decoded = _ALL_ESCAPES.sub(
            lambda m: chr(int(m.group()[1:].lstrip("xu"), 16)),
            content,
            count=_MAX_DECODED_CHARS,
        )
        return decoded if decoded != content else None
    except (ValueError, OverflowError):
        return None


def _try_decode_html_entities(content: str) -> str | None:
    """Decode HTML entities like &#105; or &#x69; that may hide injection."""
    try:
        decoded = html.unescape(content)
        return decoded if decoded != content else None
    except Exception:
        return None


def _try_decode_rot13(content: str) -> str | None:
    """Apply ROT13 — only affects ASCII letters; returns None if identity."""
    try:
        decoded = codecs.encode(content[:_MAX_DECODED_CHARS], "rot_13")
        return decoded if decoded != content else None
    except Exception:
        return None


def _try_decode_base64(content: str) -> str | None:
    """Attempt to decode base64-encoded strings with DoS guards."""
    decoded_parts: list[str] = []
    total_size = 0
    for match in _BASE64_SUSPECT.finditer(content):
        if len(decoded_parts) >= _MAX_B64_CHUNKS:
            break
        chunk = match.group()
        if len(chunk) > _MAX_B64_RAW_CHUNK:
            chunk = chunk[:_MAX_B64_RAW_CHUNK]
        try:
            decoded = base64.b64decode(chunk, validate=True).decode(
                "utf-8", errors="ignore"
            )
            if decoded and any(c.isalpha() for c in decoded):
                decoded = decoded[:_MAX_B64_CHUNK_CHARS]
                decoded_parts.append(decoded)
                total_size += len(decoded)
                if total_size > _MAX_B64_TOTAL_CHARS:
                    break
        except Exception:  # noqa: S112 — best-effort decode
            continue
    return " ".join(decoded_parts) if decoded_parts else None


def _check_homoglyphs(content: str) -> str | None:
    """Check for Cyrillic/Latin homoglyph substitution in injection keywords."""
    normalized = content
    for cyr, lat in _CYR_TO_LAT.items():
        normalized = normalized.replace(cyr, lat)
    for pattern in _INJECTION_AFTER_NORMALIZE:
        if re.search(pattern, normalized, re.IGNORECASE):
            return f"homoglyph substitution detected: {pattern}"
    return None


def _check_combining_chars(content: str) -> str | None:
    """Check for excessive combining characters."""
    if _COMBINING_RANGE.search(content):
        return "excessive combining diacritical marks"
    return None


def _check_suspicion_score(content: str) -> str | None:
    """Heuristic suspicion score — flags instruction-like structures.

    Complementary to denylist: catches novel injection patterns that don't
    match any known keyword but exhibit suspicious structural features.

    Each signal adds 1-2 points. Threshold = _SUSPICION_THRESHOLD (5).
    """
    score = 0
    signals: list[str] = []

    # Signal 1: Chat template markers (high confidence — these should never
    # appear in user content)
    template_matches = _CHAT_TEMPLATE_MARKERS.findall(content)
    if template_matches:
        score += 3
        signals.append(f"chat_template_markers({len(template_matches)})")

    # Signal 2: Role injection at start of lines (per-match scoring)
    role_matches = _ROLE_INJECTION.findall(content)
    if role_matches:
        score += min(len(role_matches), 3)  # 1 per match, max 3
        signals.append(f"role_injection({len(role_matches)})")

    # Signal 3: Imperative instruction patterns
    imperative_matches = _IMPERATIVE_PATTERNS.findall(content)
    if imperative_matches:
        score += len(imperative_matches)
        signals.append(f"imperative_patterns({len(imperative_matches)})")

    # Signal 4: System file references
    sysfile_matches = _SYSTEM_FILE_REFS.findall(content)
    if sysfile_matches:
        score += 2
        signals.append(f"system_file_refs({len(sysfile_matches)})")

    if score >= _SUSPICION_THRESHOLD:
        return f"suspicion_score={score} ({', '.join(signals)})"
    return None


def _match_patterns(
    content: str, patterns_dict: dict[str, list[str]] | None = None
) -> ScanResult | None:
    """Match content against injection patterns.

    Returns ScanResult if blocked, None if clean.
    """
    patterns = patterns_dict if patterns_dict is not None else _PATTERNS
    for category, pat_list in patterns.items():
        for pattern in pat_list:
            try:
                if re.search(pattern, content, re.IGNORECASE):
                    return ScanResult(
                        blocked=True,
                        category=category,
                        match=pattern,
                    )
            except re.error:
                continue
    return None


def _scan_decoded_recursive(
    content: str,
    filename: str,
    depth: int = 0,
    source: str = "",
    visited: set[str] | None = None,
) -> ScanResult | None:
    """Recursively scan decoded content through all decoders.

    When a decoder produces output, that output is:
    1. Checked against injection patterns directly
    2. Re-scanned through ALL other decoders (if depth < _MAX_SCAN_DEPTH)

    This prevents layered encoding bypass (e.g. base64(ROT13("ignore..."))).
    A visited set prevents decoder cycles (e.g. ROT13 involution ping-pong).
    """
    if depth >= _MAX_SCAN_DEPTH:
        return None

    if visited is None:
        visited = set()
    if content in visited:
        return None
    visited.add(content)

    # All decoders that produce text output
    decoders: list[tuple[str, Callable[[str], str | None]]] = [
        ("base64", _try_decode_base64),
        (
            "url",
            lambda c: ((d := urllib.parse.unquote(c)) != c and d) or None,
        ),
        ("html_entities", _try_decode_html_entities),
        ("hex", _try_decode_hex),
        ("unicode", _try_decode_unicode),
        ("mixed_escape", _try_decode_all_escapes),
        ("rot13", _try_decode_rot13),
    ]

    for decoder_name, decoder_fn in decoders:
        try:
            decoded = decoder_fn(content)
        except Exception:  # noqa: S112 — best-effort
            continue
        if not decoded or decoded == content:
            continue

        # Check decoded output against patterns
        result = _match_patterns(decoded)
        if result:
            prefix = f"{source}+{decoder_name}" if source else decoder_name
            result.category = (
                f"layered_{prefix}_{result.category}"
                if depth > 0
                else f"encoded_{result.category}"
            )
            result.file = filename
            result.message = (
                f"[BLOCKED] {filename}: {prefix}-encoded injection ({result.category})"
            )
            return result

        # CRITICAL: Apply suspicion score to decoded output too.
        # Without this, encoded novel injections (not in denylist) bypass both layers.
        suspicion = _check_suspicion_score(decoded)
        if suspicion:
            prefix = f"{source}+{decoder_name}" if source else decoder_name
            return ScanResult(
                blocked=True,
                category=f"layered_{prefix}_suspicion"
                if depth > 0
                else "encoded_suspicion",
                match=suspicion,
                file=filename,
                message=(
                    f"[BLOCKED] {filename}: {prefix}-encoded suspicion ({suspicion})"
                ),
            )

        # Leetspeak normalization on decoded output
        leet_norm = _normalize_leet(decoded)
        if leet_norm != decoded:
            for pattern in _INJECTION_AFTER_NORMALIZE:
                try:
                    if re.search(pattern, leet_norm, re.IGNORECASE):
                        prefix = f"{source}+{decoder_name}" if source else decoder_name
                        return ScanResult(
                            blocked=True,
                            category=f"layered_{prefix}_leetspeak"
                            if depth > 0
                            else f"{decoder_name}_leetspeak",
                            match=pattern,
                            file=filename,
                            message=(
                                f"[BLOCKED] {filename}: {prefix}+leetspeak injection"
                            ),
                        )
                except re.error:
                    continue

        # Recursive: re-scan decoded output through all decoders
        sub_result = _scan_decoded_recursive(
            decoded,
            filename,
            depth + 1,
            f"{source}+{decoder_name}" if source else decoder_name,
            visited,
        )
        if sub_result:
            return sub_result

    return None


def scan_content(content: str, filename: str = "") -> ScanResult:
    """Scan text content for prompt injection patterns.

    Multi-layer defence:
    1. Direct pattern match (denylist)
    2. Homoglyph + combining char detection
    3. Heuristic suspicion score (instruction-like structures)
    4. Recursive encoded injection detection (base64/URL/hex/unicode/ROT13/leetspeak)
    """
    if not content or not content.strip():
        return ScanResult(blocked=False)

    # Layer 1: Direct pattern match
    result = _match_patterns(content)
    if result:
        result.file = filename
        result.message = (
            f"[BLOCKED] {filename} содержал потенциальную "
            f"prompt injection ({result.category}). Контент не загружен."
        )
        logger.warning(
            "Prompt injection blocked in %s: %s (%s)",
            filename,
            result.match,
            result.category,
        )
        return result

    # Layer 2: Homoglyph check (mixed Cyrillic/Latin)
    sample = content[:200]
    has_cyrillic = any(
        "\u0400" <= c <= "\u04ff" or "\u0500" <= c <= "\u052f" for c in sample
    )
    has_latin = any(c.isascii() and c.isalpha() for c in sample)
    if has_cyrillic and has_latin:
        homoglyph_result = _check_homoglyphs(content)
        if homoglyph_result:
            logger.warning("Homoglyph injection in %s: %s", filename, homoglyph_result)
            return ScanResult(
                blocked=True,
                category="homoglyph",
                match=homoglyph_result,
                file=filename,
                message=f"[BLOCKED] {filename}: {homoglyph_result}",
            )

    # Combining characters
    combining_result = _check_combining_chars(content)
    if combining_result:
        logger.warning("Combining char abuse in %s: %s", filename, combining_result)
        return ScanResult(
            blocked=True,
            category="combining_chars",
            match=combining_result,
            file=filename,
            message=f"[BLOCKED] {filename}: {combining_result}",
        )

    # Layer 3: Heuristic suspicion score
    suspicion_result = _check_suspicion_score(content)
    if suspicion_result:
        logger.warning("Suspicion score exceeded in %s: %s", filename, suspicion_result)
        return ScanResult(
            blocked=True,
            category="heuristic_suspicion",
            match=suspicion_result,
            file=filename,
            message=f"[BLOCKED] {filename}: {suspicion_result}",
        )

    # Layer 4: Recursive encoded injection detection
    # Leetspeak on original content
    leet_normalized = _normalize_leet(content)
    if leet_normalized != content:
        for pattern in _INJECTION_AFTER_NORMALIZE:
            try:
                if re.search(pattern, leet_normalized, re.IGNORECASE):
                    return ScanResult(
                        blocked=True,
                        category="leetspeak",
                        match=pattern,
                        file=filename,
                        message=f"[BLOCKED] {filename}: leetspeak-encoded injection",
                    )
            except re.error:
                continue

    # Recursive decode scan (handles all encodings + layered encoding)
    encoded_result = _scan_decoded_recursive(content, filename)
    if encoded_result:
        logger.warning(
            "Encoded injection blocked in %s: %s", filename, encoded_result.category
        )
        return encoded_result

    return ScanResult(blocked=False)


def safe_read_context_file(path: str | None, max_chars: int = 3000) -> str | None:
    """Read context file with injection scanning. Returns None if blocked."""
    if path is None:
        return None
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return None
    try:
        content = p.read_text(encoding="utf-8")
    except Exception:
        logger.warning("Failed to read context file: %s", path)
        return None

    safe_content = content[:max_chars]
    scan = scan_content(safe_content, p.name)
    if scan.blocked:
        return None

    return safe_content
