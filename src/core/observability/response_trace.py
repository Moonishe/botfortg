"""Structured, secret-safe trace events for assistant responses."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_SECRET_KEYS = (
    "api_key",
    "authorization",
    "bot_token",
    "cookie",
    "password",
    "secret",
    "token",
)


@dataclass(slots=True)
class ResponseRouteMetrics:
    """Aggregated latency metrics for one assistant response route."""

    route: str
    call_count: int = 0
    total_latency_ms: int = 0
    max_latency_ms: int = 0
    last_latency_ms: int = 0

    @property
    def avg_latency_ms(self) -> float:
        if self.call_count <= 0:
            return 0.0
        return round(self.total_latency_ms / self.call_count, 1)


_ROUTE_METRICS: dict[str, ResponseRouteMetrics] = {}
_MAX_ROUTES = 50


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(secret in key_text for secret in _SECRET_KEYS):
                redacted[str(key)] = "***"
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, (list, tuple, set)):
        return [_redact(item) for item in value]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "..."
    return value


def _count_memory_facts(memory_context: str | None) -> int:
    if not memory_context:
        return 0
    count = 0
    for line in memory_context.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*", "•", "[")):
            count += 1
    return count


def _context_sources(memory_context: str | None) -> list[str]:
    if not memory_context:
        return []
    sources: set[str] = set()
    for marker in ("recall_context", "context_engine", "self_profile"):
        if marker in memory_context:
            sources.add(marker)
    for line in memory_context.splitlines():
        if line.startswith("[") and "]" in line:
            sources.add(line[1 : line.index("]")].split(":", 1)[0])
    return sorted(sources)


def _tool_names(items: Iterable[Any] | None) -> list[str]:
    names: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            value = item.get("tool") or item.get("intent") or item.get("action")
            if value:
                names.append(str(value))
        elif item:
            names.append(str(item))
    return names[:20]


def _record_latency(route: str, latency_ms: int | None) -> None:
    if latency_ms is None or latency_ms < 0:
        return
    key = (route or "unknown")[:80]
    metrics = _ROUTE_METRICS.get(key)
    if metrics is None:
        if len(_ROUTE_METRICS) >= _MAX_ROUTES:
            oldest = min(
                _ROUTE_METRICS.items(), key=lambda item: item[1].call_count
            )[0]
            del _ROUTE_METRICS[oldest]
        metrics = ResponseRouteMetrics(route=key)
        _ROUTE_METRICS[key] = metrics
    metrics.call_count += 1
    metrics.total_latency_ms += latency_ms
    metrics.last_latency_ms = latency_ms
    metrics.max_latency_ms = max(metrics.max_latency_ms, latency_ms)


def get_response_trace_metrics() -> dict[str, Any]:
    """Return a read-only snapshot of response route latency metrics."""
    routes = [
        {
            "route": m.route,
            "calls": m.call_count,
            "avg_ms": m.avg_latency_ms,
            "max_ms": m.max_latency_ms,
            "last_ms": m.last_latency_ms,
        }
        for m in _ROUTE_METRICS.values()
    ]
    routes.sort(key=lambda item: (item["avg_ms"], item["calls"]), reverse=True)
    return {
        "routes": routes,
        "total_calls": sum(int(item["calls"]) for item in routes),
    }


def reset_response_trace_metrics_for_test() -> None:
    """Clear response metrics. Intended for tests only."""
    _ROUTE_METRICS.clear()


def log_response_trace(
    *,
    route: str,
    owner_id: int | None = None,
    memory_context: str | None = None,
    context_sources: Iterable[str] | None = None,
    tools_proposed: Iterable[Any] | None = None,
    tools_executed: Iterable[Any] | None = None,
    tools_blocked: Iterable[Any] | None = None,
    guardrail_decision: dict[str, Any] | None = None,
    humanizer_mode: str = "off",
    humanizer_changed: bool = False,
    latency_ms: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit a compact response trace without user text or secrets."""

    _record_latency(route, latency_ms)
    sources = set(context_sources or [])
    sources.update(_context_sources(memory_context))
    payload = {
        "route": route,
        "owner_id": owner_id,
        "latency_ms": latency_ms,
        "context_sources": sorted(sources),
        "memory_facts_count": _count_memory_facts(memory_context),
        "tools_proposed": _tool_names(tools_proposed),
        "tools_executed": _tool_names(tools_executed),
        "tools_blocked": _tool_names(tools_blocked),
        "guardrail_decision": _redact(guardrail_decision or {}),
        "humanizer": {
            "mode": humanizer_mode,
            "changed": humanizer_changed,
        },
        "extra": _redact(extra or {}),
    }
    logger.info("response_trace", extra={"response_trace": payload})
