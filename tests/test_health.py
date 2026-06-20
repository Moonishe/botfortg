"""Test health_cmd has no raw SQL."""

import ast
from pathlib import Path


class TestHealthNoRawSQL:
    """Verify health_cmd.py uses no text() from sqlalchemy."""

    def test_no_text_import(self):
        """health_cmd.py should not import text from sqlalchemy."""
        path = Path("src/bot/handlers/health_cmd.py")
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy":
                for alias in node.names:
                    assert alias.name != "text", (
                        "health_cmd.py imports sqlalchemy.text (raw SQL)"
                    )

    def test_no_text_call(self):
        """health_cmd.py should not call text()."""
        path = Path("src/bot/handlers/health_cmd.py")
        source = path.read_text(encoding="utf-8")
        assert "text(" not in source, "health_cmd.py contains raw text() call"
