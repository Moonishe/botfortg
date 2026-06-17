"""LLM Error Classifier — классифицирует ошибки от LLM-провайдеров в категории.

Категории:
  - rate_limit     — 429, RateLimitError
  - auth           — 401/403, AuthenticationError
  - timeout        — TimeoutError, asyncio.TimeoutError
  - context_length — context too long, maximum context length
  - content_filter — content filter, safety
  - server_error   — 5xx
  - network        — ConnectionError, OSError
  - unknown        — всё остальное

Функция should_retry() определяет, стоит ли повторять запрос для данной категории.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# ── Константы категорий ─────────────────────────────────────────────────

CATEGORY_RATE_LIMIT: str = "rate_limit"
CATEGORY_AUTH: str = "auth"
CATEGORY_TIMEOUT: str = "timeout"
CATEGORY_CONTEXT_LENGTH: str = "context_length"
CATEGORY_CONTENT_FILTER: str = "content_filter"
CATEGORY_SERVER_ERROR: str = "server_error"
CATEGORY_NETWORK: str = "network"
CATEGORY_UNKNOWN: str = "unknown"

# ── Приоритет проверок: тип исключения > сообщение ─────────────────────

# Имена классов исключений, указывающие на конкретную категорию.
# Ключ — суффикс имени класса (lowercase), значение — категория.
_EXCEPTION_TYPE_MAP: dict[str, str] = {
    "ratelimiterror": CATEGORY_RATE_LIMIT,
    "rateexceedederror": CATEGORY_RATE_LIMIT,
    "authenticationerror": CATEGORY_AUTH,
    "permissiondeniederror": CATEGORY_AUTH,
    "unauthorizederror": CATEGORY_AUTH,
    "timeouterror": CATEGORY_TIMEOUT,
    "connectionerror": CATEGORY_NETWORK,
}

# ── Паттерны в сообщении об ошибке (lowercase match) ────────────────────

_MESSAGE_PATTERNS: list[tuple[str, str]] = [
    ("rate limit", CATEGORY_RATE_LIMIT),
    ("rate exceeded", CATEGORY_RATE_LIMIT),
    ("too many requests", CATEGORY_RATE_LIMIT),
    ("context length", CATEGORY_CONTEXT_LENGTH),
    ("context too long", CATEGORY_CONTEXT_LENGTH),
    ("maximum context length", CATEGORY_CONTEXT_LENGTH),
    ("token limit", CATEGORY_CONTEXT_LENGTH),
    ("reduce the length", CATEGORY_CONTEXT_LENGTH),
    ("content filter", CATEGORY_CONTENT_FILTER),
    ("safety", CATEGORY_CONTENT_FILTER),
    ("content_policy_violation", CATEGORY_CONTENT_FILTER),
    ("inappropriate content", CATEGORY_CONTENT_FILTER),
    ("moderation", CATEGORY_CONTENT_FILTER),
    ("server error", CATEGORY_SERVER_ERROR),
    ("service unavailable", CATEGORY_SERVER_ERROR),
    ("internal error", CATEGORY_SERVER_ERROR),
]


def _get_status_code(exc: Exception) -> int:
    """Извлечь HTTP-код из исключения, если доступен."""
    for attr in ("status_code", "http_status", "status", "code"):
        try:
            val = getattr(exc, attr, None)
            if isinstance(val, int) and val > 0:
                return val
        except (AttributeError, TypeError):
            continue
    return 0


def classify_llm_error(exception: Exception) -> str:
    """Классифицировать ошибку LLM в одну из восьми категорий.

    Порядок проверки:
      1. HTTP-статус (429/401/403/5xx).
      2. Имя класса исключения.
      3. Текст сообщения об ошибке.
      4. Исключения-обёртки (ConnectionError, asyncio.TimeoutError,
         OSError).
      5. fallback → "unknown".

    Args:
        exception: Исключение, которое нужно классифицировать.

    Returns:
        Строка-категория: "rate_limit", "auth", "timeout",
        "context_length", "content_filter", "server_error",
        "network", или "unknown".
    """
    # ── 1. HTTP-статус ──────────────────────────────────────────────
    status = _get_status_code(exception)
    if status == 429:
        return CATEGORY_RATE_LIMIT
    if status in (401, 403):
        return CATEGORY_AUTH
    if 500 <= status < 600:
        return CATEGORY_SERVER_ERROR

    # ── 2. Имя класса исключения ────────────────────────────────────
    exc_type_name = type(exception).__name__.lower()
    if exc_type_name in _EXCEPTION_TYPE_MAP:
        category = _EXCEPTION_TYPE_MAP[exc_type_name]
        logger.debug(
            "Classified %r as %s (by exception type)",
            exc_type_name,
            category,
        )
        return category

    # ── 3. Текст сообщения ──────────────────────────────────────────
    msg = str(exception).lower()
    for pattern, category in _MESSAGE_PATTERNS:
        if pattern in msg:
            logger.debug(
                "Classified error as %s (pattern: %r)",
                category,
                pattern,
            )
            return category

    # ── 4. Специальные типы (обёртки / builtins) ────────────────────
    if isinstance(exception, asyncio.TimeoutError):
        return CATEGORY_TIMEOUT
    if isinstance(exception, ConnectionError):
        return CATEGORY_NETWORK
    if isinstance(exception, OSError):
        # OSError может быть сетевой ошибкой или файловой —
        # проверяем сообщение для уточнения.
        if any(
            kw in msg
            for kw in (
                "connection",
                "socket",
                "network",
                "refused",
                "unreachable",
                "reset",
                "timeout",
                "tls",
                "ssl",
            )
        ):
            return CATEGORY_NETWORK
        return CATEGORY_UNKNOWN

    # ── 5. Fallback ─────────────────────────────────────────────────
    logger.debug("Unknown error type: %s: %s", exc_type_name, exception)
    return CATEGORY_UNKNOWN


def should_retry(category: str) -> bool:
    """Определить, нужно ли повторять запрос для данной категории ошибки.

    Retry-политика:
      - rate_limit     → True (с exponential backoff)
      - timeout        → True (1 retry)
      - server_error   → True (до 2 retries)
      - всё остальное  → False

    Args:
        category: Категория ошибки (из classify_llm_error).

    Returns:
        True если запрос можно повторить, False если нет.
    """
    return category in (
        CATEGORY_RATE_LIMIT,
        CATEGORY_TIMEOUT,
        CATEGORY_SERVER_ERROR,
    )
