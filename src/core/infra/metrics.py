"""Prometheus metrics wrapper. Zero-cost when METRICS_ENABLED is not set.

Usage::

    from src.core.infra.metrics import Counter, Gauge, MESSAGES_TOTAL
    MESSAGES_TOTAL.labels(direction="in").inc()

When ``METRICS_ENABLED`` env var is NOT set or ``prometheus_client`` is not
installed, all metric objects are silent no-ops — zero runtime overhead
beyond one env-var check at import time.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Module-level gate (same pattern as telemetry.py) ─────────────────
_METRICS_ENABLED: bool = bool(os.environ.get("METRICS_ENABLED"))

# ── Lazy imports — only load prometheus_client when actually enabled ──
_metrics_loaded: bool = False
_generate_latest: Any = None
_REGISTRY: Any = None
_Counter: Any = None
_Gauge: Any = None
_Histogram: Any = None


def _ensure_loaded() -> None:
    global _metrics_loaded, _generate_latest, _REGISTRY
    global _Counter, _Gauge, _Histogram

    if _metrics_loaded:
        return
    _metrics_loaded = True

    if not _METRICS_ENABLED:
        return

    try:
        import prometheus_client as _pc

        _generate_latest = _pc.generate_latest
        _REGISTRY = _pc.REGISTRY
        _Counter = _pc.Counter
        _Gauge = _pc.Gauge
        _Histogram = _pc.Histogram
        logger.info("Prometheus metrics enabled")
    except ImportError:
        logger.warning("METRICS_ENABLED but prometheus_client not installed")


# ── No-op metric ─────────────────────────────────────────────────────


class _NoopMetric:
    def labels(self, **kwargs: Any) -> _NoopMetric:
        return self

    def inc(self, amount: float = 1) -> None:
        pass

    def dec(self, amount: float = 1) -> None:
        pass

    def set(self, value: float) -> None:
        pass

    def observe(self, amount: float) -> None:
        pass

    def time(self) -> _NoopMetric:
        return self

    def __enter__(self) -> _NoopMetric:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


_NOOP = _NoopMetric()


# ── Public factory functions ──────────────────────────────────────────


def Counter(name: str, description: str, labelnames: list[str] | None = None) -> Any:
    _ensure_loaded()
    if _Counter is None:
        return _NOOP
    try:
        return _Counter(name, description, labelnames or [])
    except ValueError:
        return _NOOP


def Gauge(name: str, description: str, labelnames: list[str] | None = None) -> Any:
    _ensure_loaded()
    if _Gauge is None:
        return _NOOP
    try:
        return _Gauge(name, description, labelnames or [])
    except ValueError:
        return _NOOP


def Histogram(
    name: str,
    description: str,
    labelnames: list[str] | None = None,
    buckets: list[float] | None = None,
) -> Any:
    _ensure_loaded()
    if _Histogram is None:
        return _NOOP
    try:
        return _Histogram(name, description, labelnames or [], buckets=buckets)
    except ValueError:
        return _NOOP


def get_metrics() -> str:
    _ensure_loaded()
    if _generate_latest is None or _REGISTRY is None:
        return ""
    try:
        return _generate_latest(_REGISTRY).decode("utf-8")
    except Exception:
        logger.debug("generate_latest failed", exc_info=True)
        return ""


# ── Metrics server ────────────────────────────────────────────────────


async def start_metrics_server(port: int = 9090) -> None:
    """Start aiohttp server exposing GET /metrics on ``port``."""
    from aiohttp import web

    async def _handler(_request: Any) -> Any:
        return web.Response(
            text=get_metrics(), content_type="text/plain; version=0.0.4"
        )

    app = web.Application()
    app.router.add_get("/metrics", _handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    logger.info("Metrics server on 127.0.0.1:%d/metrics", port)

    try:
        stop_event = asyncio.Event()
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.debug("Metrics server shutting down")
    finally:
        await runner.cleanup()


# ── Pre-created metrics ───────────────────────────────────────────────


MESSAGES_TOTAL = Counter(
    "telegram_messages_total",
    "Total messages processed",
    labelnames=["direction"],
)
