"""Account Usage Tracker — LLM token usage & cost tracker across providers.

Singleton with async-safe counters. Tracks rolling windows: today, week, month.
Cost resolution via COST_PER_1K pricing dictionary for popular models.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

# ── Pricing per 1K tokens (USD), mid-2025 approximate ────────────────────
# Structure: COST_PER_1K[provider][model] = {"in": ..., "out": ...}
# Falls back to "default" key within each provider.

COST_PER_1K: dict[str, dict[str, dict[str, float]]] = {
    "deepseek": {
        "deepseek-chat": {"in": 0.00014, "out": 0.00028},
        "deepseek-reasoner": {"in": 0.00055, "out": 0.00219},
        "default": {"in": 0.00014, "out": 0.00028},
    },
    "openai": {
        "gpt-4o": {"in": 0.0025, "out": 0.01},
        "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
        "gpt-4.1": {"in": 0.002, "out": 0.008},
        "gpt-4.1-mini": {"in": 0.0004, "out": 0.0016},
        "gpt-4.1-nano": {"in": 0.0001, "out": 0.0004},
        "o3": {"in": 0.01, "out": 0.04},
        "o4-mini": {"in": 0.0011, "out": 0.0044},
        "default": {"in": 0.0025, "out": 0.01},
    },
    "claude": {
        "claude-3-opus": {"in": 0.015, "out": 0.075},
        "claude-3.5-sonnet": {"in": 0.003, "out": 0.015},
        "claude-3.5-haiku": {"in": 0.0008, "out": 0.004},
        "claude-sonnet-4": {"in": 0.003, "out": 0.015},
        "default": {"in": 0.003, "out": 0.015},
    },
    "gemini": {
        "gemini-2.5-flash": {"in": 0.00015, "out": 0.0006},
        "gemini-2.5-pro": {"in": 0.00125, "out": 0.01},
        "gemini-2.0-flash": {"in": 0.0001, "out": 0.0004},
        "default": {"in": 0.00015, "out": 0.0006},
    },
    "grok": {
        "grok-3": {"in": 0.003, "out": 0.015},
        "grok-3-mini": {"in": 0.0003, "out": 0.0005},
        "default": {"in": 0.003, "out": 0.015},
    },
}

# ── Time window helpers ───────────────────────────────────────────────────


def _day_start() -> float:
    now = time.localtime()
    return time.mktime(
        (
            now.tm_year,
            now.tm_mon,
            now.tm_mday,
            0,
            0,
            0,
            0,
            0,
            now.tm_isdst,
        )
    )


def _week_start() -> float:
    now = time.localtime()
    day_ts = time.mktime(
        (
            now.tm_year,
            now.tm_mon,
            now.tm_mday,
            0,
            0,
            0,
            0,
            0,
            now.tm_isdst,
        )
    )
    return day_ts - (now.tm_wday * 86400)


def _month_start() -> float:
    now = time.localtime()
    return time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, now.tm_isdst))


def _resolve_cost(provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
    pp = COST_PER_1K.get(provider, {})
    mp = pp.get(model, pp.get("default", {"in": 0.0, "out": 0.0}))
    return (tokens_in / 1000) * mp["in"] + (tokens_out / 1000) * mp["out"]


# ── Lightweight record ────────────────────────────────────────────────────


class _UsageRecord:
    __slots__ = ("cost", "model", "provider", "tokens_in", "tokens_out", "ts")

    def __init__(
        self,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost: float,
        ts: float,
    ) -> None:
        self.provider = provider
        self.model = model
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.cost = cost
        self.ts = ts


# ── Singleton tracker ─────────────────────────────────────────────────────


class AccountUsageTracker:
    """Singleton: tracks tokens, costs, and call counts for today/week/month.

    Async-safe via asyncio.Lock. Records pruned after 35 days to bound memory.
    """

    _instance: AccountUsageTracker | None = None

    def __new__(cls) -> AccountUsageTracker:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_records"):
            return
        self._records: list[_UsageRecord] = []
        self._lock: asyncio.Lock | None = None

    @property
    def _async_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ── Public API ────────────────────────────────────────────────────────

    async def record_usage(
        self,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        """Record a single successful LLM call."""
        cost = _resolve_cost(provider, model, tokens_in, tokens_out)
        rec = _UsageRecord(provider, model, tokens_in, tokens_out, cost, time.time())
        async with self._async_lock:
            self._records.append(rec)
            # Prune records older than 35 days
            cutoff = time.time() - 35 * 86400
            self._records = [r for r in self._records if r.ts >= cutoff]

    async def get_usage_report(self) -> str:
        """Human-readable usage report for today / week / month."""
        day = await self._aggregate(_day_start())
        week = await self._aggregate(_week_start())
        month = await self._aggregate(_month_start())

        def _prov(p: dict[str, int]) -> str:
            if not p:
                return "—"
            return ", ".join(f"{k}:{v}" for k, v in sorted(p.items()))

        return (
            "📊 **Usage Report**\n"
            "```\n"
            f"{'':<8} {'Tokens':>10} {'Cost':>10} {'Calls':>7}  Providers\n"
            f"{'─' * 55}\n"
            f"{'Today':<8} {day['tokens']:>10,} ${day['cost']:>9.4f}"
            f" {day['calls']:>7}  {_prov(day['by_provider'])}\n"
            f"{'Week':<8} {week['tokens']:>10,} ${week['cost']:>9.4f}"
            f" {week['calls']:>7}  {_prov(week['by_provider'])}\n"
            f"{'Month':<8} {month['tokens']:>10,} ${month['cost']:>9.4f}"
            f" {month['calls']:>7}  {_prov(month['by_provider'])}\n"
            "```"
        )

    async def check_limits(
        self,
        daily_limit: int = 100_000,
        monthly_limit: int = 1_000_000,
    ) -> dict:
        """Check usage against limits. Returns {'ok': bool, 'warnings': [...]}."""
        day = await self._aggregate(_day_start())
        month = await self._aggregate(_month_start())
        warnings: list[str] = []

        if day["tokens"] >= daily_limit:
            warnings.append(
                f"Daily limit reached: {day['tokens']:,}/{daily_limit:,} tokens",
            )
        elif day["tokens"] >= daily_limit * 0.8:
            warnings.append(
                f"Daily limit ≥80%: {day['tokens']:,}/{daily_limit:,} tokens",
            )

        if month["tokens"] >= monthly_limit:
            warnings.append(
                f"Monthly limit reached: {month['tokens']:,}/{monthly_limit:,} tokens",
            )
        elif month["tokens"] >= monthly_limit * 0.8:
            warnings.append(
                f"Monthly limit ≥80%: {month['tokens']:,}/{monthly_limit:,} tokens",
            )

        return {"ok": len(warnings) == 0, "warnings": warnings}

    # ── Internal ──────────────────────────────────────────────────────────

    async def _aggregate(self, since: float) -> dict:
        tokens = 0
        cost = 0.0
        calls = 0
        by_provider: dict[str, int] = defaultdict(int)
        async with self._async_lock:
            for r in self._records:
                if r.ts >= since:
                    tokens += r.tokens_in + r.tokens_out
                    cost += r.cost
                    calls += 1
                    by_provider[r.provider] += 1
        return {
            "tokens": tokens,
            "cost": cost,
            "calls": calls,
            "by_provider": dict(by_provider),
        }


def get_tracker() -> AccountUsageTracker:
    """Convenience accessor for the singleton."""
    return AccountUsageTracker()
