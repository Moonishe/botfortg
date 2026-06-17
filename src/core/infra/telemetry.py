"""OpenTelemetry tracing wrapper. Zero-cost when no exporter configured.

Usage::

    from src.core.infra.telemetry import start_span

    with start_span("operation.name", some_attr="value"):
        ...

When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is NOT set or packages are not installed,
all functions are graceful no-ops — no runtime overhead beyond one env-var check.

Optional dependencies (install them before enabling)::

    pip install opentelemetry-api opentelemetry-sdk
    opentelemetry-exporter-otlp-proto-http
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_TRACING_ENABLED: bool = bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))

# Lazy imports — only load the SDK when tracing is actually enabled.
_tracer: object | None = None
_import_error: str | None = None


def _get_tracer() -> object | None:
    """Lazy-init the OTel tracer. Returns None when disabled or unavailable."""
    global _tracer, _import_error
    if _tracer is not None:
        return _tracer
    if not _TRACING_ENABLED:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        provider = TracerProvider()
        exporter = OTLPSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("telegram-helper")
    except ImportError as exc:
        _import_error = str(exc)
        _tracer = None  # stay disabled
    return _tracer


def start_span(name: str, **attrs: object):
    """Start a span. No-op when tracing is disabled or packages are missing.

    Use as a context manager::

        with start_span("message.process", user_id="12345"):
            ...
    """
    tracer = _get_tracer()
    if tracer is None:
        return _noop_span()
    # tracer is an opentelemetry.trace.Tracer
    return tracer.start_as_current_span(name, attributes=attrs)  # type: ignore[union-attr]


@contextmanager
def _noop_span():
    """Yield None — a zero-overhead substitute for a real span."""
    yield None


def set_attribute(key: str, value: object) -> None:
    """Set attribute on the currently active span. No-op if no active span."""
    if not _TRACING_ENABLED:
        return
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording:
            span.set_attribute(key, value)
    except Exception:
        logger.debug("telemetry.set_attribute failed for key=%s", key, exc_info=True)


def add_event(name: str, **attrs: object) -> None:
    """Add a timestamped event to the current span. No-op if tracing disabled."""
    if not _TRACING_ENABLED:
        return
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording:
            span.add_event(name, attributes=attrs)
    except Exception:
        logger.debug("telemetry.add_event failed for name=%s", name, exc_info=True)
