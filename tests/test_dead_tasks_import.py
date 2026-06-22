"""Smoke test: verify all modules from _register_background_tasks() import successfully.

Tests that the two modules registered in main.py's _register_background_tasks()
can be imported without ImportError. This catches dead-import regressions
(e.g., after refactoring settings access, removing handlers, etc.).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")

import pytest


class TestBackgroundTasksImports:
    """Verify every module touched by _register_background_tasks() imports."""

    def test_free_text_register_cleanup_timer_importable(self) -> None:
        """PERF-018: register_cleanup_timer must be importable and callable."""
        from src.bot.handlers.free_text import register_cleanup_timer

        assert callable(register_cleanup_timer), (
            "register_cleanup_timer is not callable"
        )

    def test_memory_consolidator_importable(self) -> None:
        """Memory consolidator side-effect import must succeed.

        The module-level ``@task_manager.task()`` decorator triggers on import;
        a broken import means consolidator never registers.
        """
        import src.core.memory.memory_consolidator  # noqa: F401

        assert src.core.memory.memory_consolidator is not None

    def test_consolidator_exports_consolidate_memories(self) -> None:
        """consolidate_memories() must be importable from the consolidator module."""
        from src.core.memory.memory_consolidator import (
            consolidate_memories,
        )

        assert callable(consolidate_memories), "consolidate_memories is not callable"
