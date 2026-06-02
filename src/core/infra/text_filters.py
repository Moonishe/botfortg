"""Text-based guards shared across modules."""

_NO_SEARCH_PATTERNS = (
    "не гугл",
    "не ищи",
    "без интернет",
    "ответь сам",
    "не надо гугл",
    "без гугл",
    "не надо гуглить",
    "без поиска",
    "не ищи в гугле",
    "ответь из головы",
    "из головы ответь",
    "из головы скажи",
    "без серча",
    "no search",
    "no google",
    "не лезь в интернет",
)


def should_skip_web_search(user_text: str) -> bool:
    """Check if user explicitly forbade web search.

    Matches against transliterated Russian patterns that mean
    "don't Google", "don't search", "answer from memory", etc.
    """
    if not user_text:
        return False
    text_lower = user_text.lower()
    return any(p in text_lower for p in _NO_SEARCH_PATTERNS)
