"""Prompt guard: boundary delimiters and PII/secrets scrubbing.

Utilities to reduce prompt injection risk and prevent accidental logging of
sensitive user data in trajectory/session storage.
"""

from __future__ import annotations

import re


def fence_user_text(text: str) -> str:
    """Wrap raw user content in XML-style delimiters.

    Helps the model distinguish user-controlled input from system instructions.
    """
    if not text:
        return "<user_input></user_input>"
    # Normalize tags inside the text so the closing delimiter cannot be escaped.
    safe = text.replace("</user_input>", "<SLASH user_input>")
    return f"<user_input>\n{safe}\n</user_input>"


# Loose but safe patterns — designed to catch common PII/secrets without
# destroying regular text.
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", flags=re.ASCII
)
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s\-()]*)?(?:\(?\d{1,4}\)?[\s\-]*)?(?:\d[\s\-]*){7,15}",
    flags=re.ASCII,
)
_SECRET_RE = re.compile(
    r"\b(api[_\-]?key|apikey|token|secret|password|pwd)\s*[=:]\s*[^\s\'\"]{8,}\b",
    flags=re.IGNORECASE,
)
_TOKEN_RE = re.compile(
    r"\b(?:sk|pk|ghp|gho|ghu|ghs|ghr|glpat|AKIA|RGAPI|bearer)\-[A-Za-z0-9_\-]{10,}\b",
    flags=re.IGNORECASE,
)


def sanitize_pii(text: str) -> str:
    """Scrub common PII and secrets from text before storing/logging.

    Returns a redacted copy; the original text is untouched.
    """
    if not text:
        return text
    text = _TOKEN_RE.sub("[TOKEN]", text)
    text = _SECRET_RE.sub(r"\1=[REDACTED]", text)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    return text
