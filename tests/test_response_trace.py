import logging

from src.core.observability.response_trace import (
    get_response_trace_metrics,
    log_response_trace,
    reset_response_trace_metrics_for_test,
)

REDACTED = "***"


def test_response_trace_redacts_secrets_and_counts_memory(caplog) -> None:
    reset_response_trace_metrics_for_test()
    caplog.set_level(logging.INFO, logger="src.core.observability.response_trace")

    log_response_trace(
        route="maestro",
        owner_id=123,
        memory_context="- [recall_context] loves coffee\nplain line",
        tools_proposed=[{"tool": "mcp_system"}],
        tools_executed=["mcp_system"],
        tools_blocked=[],
        guardrail_decision={"risk": "low", "api_token": "super-secret"},
        humanizer_mode="fix",
        humanizer_changed=True,
        latency_ms=1500,
        extra={"nested": {"password": "hidden"}},
    )

    payload = caplog.records[0].response_trace

    assert payload["route"] == "maestro"
    assert payload["memory_facts_count"] == 1
    assert payload["tools_proposed"] == ["mcp_system"]
    assert payload["latency_ms"] == 1500
    assert payload["humanizer"] == {"mode": "fix", "changed": True}
    assert payload["guardrail_decision"]["api_token"] == REDACTED
    assert payload["extra"]["nested"]["password"] == REDACTED

    metrics = get_response_trace_metrics()
    assert metrics["total_calls"] == 1
    assert metrics["routes"][0]["route"] == "maestro"
    assert metrics["routes"][0]["avg_ms"] == 1500.0


def test_response_trace_metrics_ignore_missing_latency() -> None:
    reset_response_trace_metrics_for_test()

    log_response_trace(route="cache_hit")

    assert get_response_trace_metrics() == {"routes": [], "total_calls": 0}
