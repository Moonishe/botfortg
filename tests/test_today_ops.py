"""Tests for the compact ops block inside /today."""

from __future__ import annotations

from src.bot.handlers.today_cmd import _format_ops_lines_for_today


def test_format_ops_lines_for_today_empty_alerts() -> None:
    assert _format_ops_lines_for_today([]) == []


def test_format_ops_lines_for_today_limits_and_escapes() -> None:
    lines = _format_ops_lines_for_today(
        [
            "DB <down>",
            "Security warning",
            "Queue pressure",
            "Tool errors",
        ]
    )
    text = "\n".join(lines)

    assert "DB &lt;down&gt;" in text
    assert "DB <down>" not in text
    assert "Tool errors" not in text
    assert "Ещё 1: /ops" in text
