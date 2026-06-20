"""Test mcp_self_info has no raw SQL."""

from pathlib import Path


class TestMcpSelfInfoNoRawSQL:
    """Verify mcp_self_info.py uses no text() from sqlalchemy for liveness check."""

    def test_no_text_call(self):
        """mcp_self_info.py should not call text() for SELECT 1."""
        path = Path("src/core/actions/mcp_self_info.py")
        source = path.read_text(encoding="utf-8")
        # Must not contain text("SELECT 1") pattern
        assert 'text("SELECT 1")' not in source, (
            "mcp_self_info.py contains raw text() call"
        )
        assert "sql_text" not in source, "mcp_self_info.py imports text as sql_text"

    def test_uses_select_instead(self):
        """mcp_self_info.py should use select(1) instead."""
        path = Path("src/core/actions/mcp_self_info.py")
        source = path.read_text(encoding="utf-8")
        # Verify select is used for DB liveness
        assert "select" in source.lower(), (
            "mcp_self_info.py should use select() for DB queries"
        )
