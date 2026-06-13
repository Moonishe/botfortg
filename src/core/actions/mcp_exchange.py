"""mcp_exchange — актуальные курсы валют через open.er-api.com (бесплатно, без ключа).

Actions:
- ``action="get_rates" base="USD"`` — курсы для базовой валюты
- ``action="convert" amount=100 from_currency="USD" to_currency="RUB"`` — конвертация
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)

_API_URL = "https://open.er-api.com/v6/latest"
_TIMEOUT = 6.0  # seconds

# ── Основные валюты для удобочитаемой выдачи ──────────────────────────────
_COMMON = frozenset(
    {
        "USD",
        "EUR",
        "RUB",
        "GBP",
        "JPY",
        "CNY",
        "CHF",
        "CAD",
        "AUD",
        "INR",
        "BRL",
        "TRY",
        "KZT",
        "UAH",
        "BYN",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "CZK",
        "HUF",
        "GEL",
        "AMD",
        "UZS",
        "KGS",
        "TJS",
        "AZN",
    }
)


# ══════════════════════════════════════════════════════════════════════════
# Tool: mcp_exchange
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="mcp_exchange",
    description=(
        "Актуальные курсы валют через open.er-api.com (бесплатно, без API-ключа). "
        "Два действия:\n"
        "- 'get_rates' — получить курсы для всех валют относительно базовой "
        "(по умолчанию USD).\n"
        "- 'convert' — конвертировать сумму из одной валюты в другую. "
        "Пример: action='convert' amount=100 from_currency='USD' to_currency='RUB'"
    ),
    category="search",
    risk="low",
    params={
        "action": "str — 'get_rates' или 'convert'",
        "base": "str — базовая валюта для get_rates (по умолчанию 'USD')",
        "amount": "float — сумма для convert",
        "from_currency": "str — исходная валюта для convert",
        "to_currency": "str — целевая валюта для convert",
    },
)
async def mcp_exchange(
    action: str = "get_rates",
    base: str = "USD",
    amount: float = 0.0,
    from_currency: str = "USD",
    to_currency: str = "RUB",
    **kwargs: Any,
) -> dict[str, Any]:
    """Курсы валют и конвертация.

    Args:
        action: ``"get_rates"`` или ``"convert"``.
        base: Код базовой валюты (по умолчанию ``"USD"``).
        amount: Сумма для конвертации.
        from_currency: Исходная валюта.
        to_currency: Целевая валюта.

    Returns:
        dict с ``ok`` / ``error`` и данными.
    """
    try:
        base = base.upper().strip()

        if action == "get_rates":
            return await _get_rates(base)
        elif action == "convert":
            return await _convert(
                amount, from_currency.upper().strip(), to_currency.upper().strip()
            )
        else:
            return {
                "error": f"Неизвестное действие: {action!r}. "
                f"Допустимые: get_rates, convert"
            }
    except Exception as exc:
        logger.exception("mcp_exchange(%r) failed", action)
        return {"error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════
# Action implementations
# ══════════════════════════════════════════════════════════════════════════


async def _fetch_rates(base: str) -> dict[str, Any]:
    """Загрузить курсы для *base* через open.er-api.com."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_API_URL}/{base}")
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
        raise ValueError(msg) from e
    except httpx.RequestError as e:
        raise ValueError(f"Сетевая ошибка: {e}") from e


async def _get_rates(base: str) -> dict[str, Any]:
    """Получить курсы для базовой валюты."""
    data = await _fetch_rates(base)
    if data.get("result") != "success":
        return {"error": f"API вернул ошибку для {base}"}

    all_rates: dict[str, float] = data.get("rates", {})
    common = {c: r for c, r in all_rates.items() if c in _COMMON}
    update_time = data.get("time_last_update_utc", "")

    return {
        "ok": True,
        "base": data.get("base_code", base),
        "rates": common,
        "rates_count": len(common),
        "total_currencies": len(all_rates),
        "updated": update_time,
    }


async def _convert(
    amount: float, from_currency: str, to_currency: str
) -> dict[str, Any]:
    """Конвертировать сумму из одной валюты в другую."""
    data = await _fetch_rates(from_currency)
    if data.get("result") != "success":
        return {"error": f"API вернул ошибку для {from_currency}"}

    rates: dict[str, float] = data.get("rates", {})
    rate = rates.get(to_currency)
    if rate is None:
        return {"error": f"Валюта {to_currency!r} не найдена"}

    result = round(amount * rate, 4)
    update_time = data.get("time_last_update_utc", "")

    return {
        "ok": True,
        "amount": amount,
        "from": from_currency,
        "to": to_currency,
        "rate": rate,
        "result": result,
        "updated": update_time,
    }
