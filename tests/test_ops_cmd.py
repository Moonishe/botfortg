"""Tests for /ops operational dashboard helpers."""

from __future__ import annotations

import pytest

from src.bot.handlers import ops_cmd


def _healthy_snapshot() -> dict:
    return {
        "db": {"ok": True, "size_mb": 1.2},
        "tasks": {"total": 2, "running": 2, "failed": [], "failures": 0},
        "security": {
            "overall": "secure",
            "counts": {"ok": 8, "warning": 0, "critical": 0, "info": 0},
        },
        "caches": {"total": 3, "pressure": []},
        "circuits": {
            "total": 2,
            "states": {"open": 0, "half_open": 0, "closed": 2},
        },
        "tools": {"tools": 1, "calls": 2, "errors": 0, "slow": []},
        "responses": {"routes": [], "total_calls": 0},
        "memory_queue": {
            "size": 0,
            "max_size": 200,
            "dlq_size": 0,
            "dlq_max_size": 50,
        },
    }


def test_format_ops_snapshot_healthy() -> None:
    text = ops_cmd.format_ops_snapshot(_healthy_snapshot())

    assert "Ops Dashboard" in text
    assert "DB: OK, 1.2 MB" in text
    assert "Tasks: 2/2 running" in text
    assert "No immediate alerts." in text


def test_format_ops_snapshot_alerts_are_html_escaped() -> None:
    snapshot = _healthy_snapshot()
    snapshot["db"] = {"ok": False, "error": "bad<db>"}
    snapshot["tasks"] = {
        "total": 2,
        "running": 1,
        "failed": ["task<one>"],
        "failures": 1,
    }
    snapshot["security"] = {
        "overall": "warning",
        "counts": {"ok": 6, "warning": 2, "critical": 0, "info": 0},
    }
    snapshot["caches"] = {
        "total": 1,
        "pressure": [{"name": "cache<hot>", "size": 9, "max_size": 10}],
    }
    snapshot["circuits"] = {
        "total": 1,
        "states": {"open": 1, "half_open": 0, "closed": 0},
    }
    snapshot["tools"] = {
        "tools": 1,
        "calls": 3,
        "errors": 1,
        "slow": [{"name": "tool<slow>", "avg_ms": 123.4, "calls": 3}],
    }
    snapshot["memory_queue"] = {
        "size": 9,
        "max_size": 10,
        "dlq_size": 1,
        "dlq_max_size": 50,
    }

    text = ops_cmd.format_ops_snapshot(snapshot)

    assert "bad&lt;db&gt;" in text
    assert "task&lt;one&gt;" in text
    assert "cache&lt;hot&gt;" in text
    assert "tool&lt;slow&gt;" in text
    assert "bad<db>" not in text
    assert "Memory queue is near capacity" in text


def test_format_ops_snapshot_includes_response_metrics() -> None:
    snapshot = _healthy_snapshot()
    snapshot["responses"] = {
        "total_calls": 3,
        "routes": [
            {
                "route": "maestro<default>",
                "calls": 3,
                "avg_ms": 1200.5,
                "max_ms": 2000,
                "last_ms": 800,
            }
        ],
    }

    text = ops_cmd.format_ops_snapshot(snapshot)

    assert "Responses: 3 calls" in text
    assert "maestro&lt;default&gt; 1200.5ms avg" in text


def test_format_ops_snapshot_collector_error_alert() -> None:
    snapshot = _healthy_snapshot()
    snapshot["tools"] = {"error": "ValueError"}

    text = ops_cmd.format_ops_snapshot(snapshot)

    assert "tools collector failed: ValueError" in text


def test_ops_alerts_for_snapshot_reuses_alert_rules() -> None:
    snapshot = _healthy_snapshot()
    snapshot["db"] = {"ok": False, "error": "OperationalError"}

    alerts = ops_cmd.ops_alerts_for_snapshot(snapshot)

    assert alerts == ["DB check failed: OperationalError"]


@pytest.mark.asyncio
async def test_collect_ops_snapshot_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def collect_db(_telegram_id: int) -> dict:
        raise RuntimeError("db boom")

    async def collect_tasks() -> dict:
        return {"total": 1, "running": 1, "failed": [], "failures": 0}

    async def collect_security() -> dict:
        return {"overall": "secure", "counts": {}}

    async def collect_caches() -> dict:
        return {"total": 0, "pressure": []}

    async def collect_circuits() -> dict:
        return {"total": 0, "states": {"open": 0, "half_open": 0, "closed": 0}}

    async def collect_tools() -> dict:
        return {"tools": 0, "calls": 0, "errors": 0, "slow": []}

    async def collect_responses() -> dict:
        return {"routes": [], "total_calls": 0}

    async def collect_memory_queue() -> dict:
        return {"size": 0, "max_size": 1, "dlq_size": 0, "dlq_max_size": 50}

    monkeypatch.setattr(ops_cmd, "_collect_db", collect_db)
    monkeypatch.setattr(ops_cmd, "_collect_tasks", collect_tasks)
    monkeypatch.setattr(ops_cmd, "_collect_security", collect_security)
    monkeypatch.setattr(ops_cmd, "_collect_caches", collect_caches)
    monkeypatch.setattr(ops_cmd, "_collect_circuits", collect_circuits)
    monkeypatch.setattr(ops_cmd, "_collect_tools", collect_tools)
    monkeypatch.setattr(ops_cmd, "_collect_responses", collect_responses)
    monkeypatch.setattr(ops_cmd, "_collect_memory_queue", collect_memory_queue)

    snapshot = await ops_cmd.collect_ops_snapshot(123)

    assert snapshot["db"] == {"error": "RuntimeError"}
    assert snapshot["tasks"]["running"] == 1
    assert snapshot["memory_queue"]["max_size"] == 1


@pytest.mark.asyncio
async def test_memory_queue_stats_shape() -> None:
    from src.core.memory._queue_core import get_queue_stats

    stats = await get_queue_stats()

    assert set(stats) == {"size", "max_size", "dlq_size", "dlq_max_size"}
    assert stats["max_size"] >= 1
    assert stats["dlq_max_size"] >= 1
