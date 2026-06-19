"""Mask API keys in error strings and log messages."""

import re
import logging

logger = logging.getLogger(__name__)

# API key patterns
_KEY_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",  # OpenAI/DeepSeek/etc
    r"sk-proj-[A-Za-z0-9]{20,}",  # OpenAI project key
    r"sk-ant-api03-[A-Za-z0-9_-]{20,}",  # Anthropic
    r"sk-or-[A-Za-z0-9]{20,}",  # OpenRouter
    r"AIza[A-Za-z0-9_-]{30,}",  # Gemini
    r"xai-[A-Za-z0-9]{20,}",  # Grok
    r"gsk_[A-Za-z0-9]{20,}",  # Groq
    r"dl-[A-Za-z0-9_-]{20,}",  # Deepgram
    r"Nb[A-Za-z0-9_-]{20,}",  # Mistral (some formats)
    r"\d{8,10}:[\w-]{35,}",  # Telegram bot token
    r"[A-Za-z0-9_-]{43}=",  # Fernet KEK/DEK (44 urlsafe-base64 chars)
]

_MASKED_REPLACEMENT = "***"

# PII patterns — applied to LLM-bound text only (not logs, where PII may be needed for debugging)
# Order matters: longest/most-specific patterns FIRST to prevent partial masking by shorter ones.
_PII_PATTERNS = [
    re.compile(
        r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"
    ),  # credit card (16 digits) — BEFORE phone
    re.compile(
        r"\b\d{3}[ -]?\d{3}[ -]?\d{3}[ -]?\d{2}\b"
    ),  # СНИЛС (11 digits: XXX-XXX-XXX YY) — BEFORE phone
    re.compile(r"\b\d{10}\b|\b\d{12}\b"),  # ИНН (10/12 digits) — BEFORE phone
    re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    ),  # IPv4 (strict: 0-255 per octet)
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}"),  # email
    re.compile(r"\+7[ -]?\d{3}[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}"),  # Russian phone +7XXX
    re.compile(
        r"\+?\d{1,3}[ -]?\d{3}[ -]?\d{3}[ -]?\d{2,4}"
    ),  # generic international phone — LAST (shortest, may partial-match)
    re.compile(
        r"(?<!\w)@(?!(?:dataclass|staticmethod|classmethod|property|abstractmethod|"
        r"contextmanager|cached_property|wraps|overload|final|override|lru_cache|"
        r"singledispatch|asynccontextmanager|abstractproperty|"
        r"tool|app|router|bp|get|post|put|delete|patch)\b|\w+\.)\w{5,32}\b"
    ),  # Telegram @username (not decorator / dotted decorator @module.name)
]


def mask_keys(text: str) -> str:
    """Replace all API keys in string with ***."""
    if not text or not isinstance(text, str):
        return text
    for pattern in _KEY_PATTERNS:
        text = re.sub(pattern, _MASKED_REPLACEMENT, text)
    return text


def mask_pii(text: str) -> str:
    """Mask PII (email, phone, @username) in text bound for external LLM.

    Chains with mask_keys: mask_pii(mask_keys(text)) for full protection.
    ponytail: separate from mask_keys — logs may need PII for debugging, LLM never does.
    """
    if not text or not isinstance(text, str):
        return text
    for pattern in _PII_PATTERNS:
        text = pattern.sub(_MASKED_REPLACEMENT, text)
    return text


class KeyMaskFilter(logging.Filter):
    """Logging-фильтр: маскирует API-ключи в каждом log-сообщении."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_keys(record.msg)
        if record.args:
            record.args = tuple(
                mask_keys(a) if isinstance(a, str) else a for a in record.args
            )
        return True


def safe_str(exc: Exception) -> str:
    """Safe str() — masks keys."""
    return mask_keys(str(exc))


def safe_repr(exc: Exception) -> str:
    """Safe repr() — masks keys."""
    return mask_keys(repr(exc))
