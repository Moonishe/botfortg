"""Canonical provider display names.

# ponytail: single source of truth for LLM provider labels used by bot UI.
"""

from __future__ import annotations


PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "openai": "OpenAI",
    "gemini": "Gemini",
    "mistral": "Mistral",
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "grok": "Grok (xAI)",
    "groq": "Groq",
    "mimo": "MiMo (Xiaomi)",
    "cloudflare": "Cloudflare",
    "openrouter": "OpenRouter",
    "custom": "Свой провайдер",
}


# Backwards-compat alias for `_pretty_provider` callers.
PRETTY_PROVIDER_NAMES: dict[str, str] = {
    **PROVIDER_DISPLAY_NAMES,
    "openrouter": "OpenRouter (DeepSeek V4)",
}


def provider_display_name(
    provider: str | None, *, pretty_openrouter: bool = False
) -> str:
    """Return a human-readable provider name.

    Args:
        provider: raw provider key (e.g. "openai").
        pretty_openrouter: if True, return "OpenRouter (DeepSeek V4)" for openrouter.
                           Preserves the old `_pretty_provider` behavior.
    """
    if not provider:
        return "—"
    table = PRETTY_PROVIDER_NAMES if pretty_openrouter else PROVIDER_DISPLAY_NAMES
    return table.get(provider, provider)
