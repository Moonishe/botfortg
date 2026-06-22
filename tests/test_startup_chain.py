"""Smoke test: verify key imports from main.py do not raise ImportError.

After the settings cleanup (removal of 81 handler imports, os.getenv→settings,
Sequence[str] annotations, etc.) several imports could silently break.
This test verifies the startup import chain without actually running the event loop.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")

import pytest


# ── Top-level imports from main.py ────────────────────────────────────────


class TestTopLevelImports:
    """Module-level imports that run at interpreter start (before main())."""

    def test_bot_app_import(self) -> None:
        from src.bot.app import run_bot, run_bot_webhook

        assert callable(run_bot)
        assert callable(run_bot_webhook)

    def test_bot_voice_worker_import(self) -> None:
        from src.bot import start_voice_worker, stop_voice_worker

        assert callable(start_voice_worker)
        assert callable(stop_voice_worker)

    def test_app_context_import(self) -> None:
        from src.core.infra.app_context import get_app_context

        ctx = get_app_context()
        assert ctx is not None

    def test_memory_queue_import(self) -> None:
        from src.core.memory.memory_queue import start_worker, stop_worker

        assert callable(start_worker)
        assert callable(stop_worker)

    def test_task_manager_import(self) -> None:
        from src.core.infra.task_manager import task_manager

        assert task_manager is not None

    def test_auto_save_batch_import(self) -> None:
        from src.core.memory.auto_save_batch import get_batch_buffer

        assert callable(get_batch_buffer)

    def test_db_session_import(self) -> None:
        from src.db.session import init_db

        assert callable(init_db)

    def test_userbot_manager_import(self) -> None:
        from src.userbot.manager import UserbotManager

        assert UserbotManager is not None

    def test_settings_import(self) -> None:
        from src.config import PROJECT_ROOT, settings

        assert settings is not None
        assert PROJECT_ROOT is not None


# ── Lazy / deferred imports from main() body ──────────────────────────────


class TestDeferredImports:
    """Imports that happen inside main() and its helpers."""

    def test_key_guard_import(self) -> None:
        from src.core.infra.key_guard import KeyMaskFilter

        assert KeyMaskFilter is not None

    def test_callback_utils_import(self) -> None:
        import src.bot.callback_utils  # noqa: F401

        assert src.bot.callback_utils is not None

    def test_snapshot_engine_import(self) -> None:
        from src.core.state.snapshot_engine import snapshot_engine

        assert snapshot_engine is not None

    def test_humanizer_import(self) -> None:
        from src.core.humanizer.humanizer import load_humanizer_feedback

        assert callable(load_humanizer_feedback)

    def test_circuit_telemetry_import(self) -> None:
        from src.core.observability.circuit_telemetry import circuit_telemetry

        assert circuit_telemetry is not None

    def test_tool_middleware_import(self) -> None:
        from src.core.actions.tool_middleware import (
            DecisionRepairGuard,
            ToolCircuitBreaker,
        )

        assert ToolCircuitBreaker is not None
        assert DecisionRepairGuard is not None

    def test_llm_router_import(self) -> None:
        from src.llm.router import ensure_locks_initialized

        assert callable(ensure_locks_initialized)

    def test_gating_import(self) -> None:
        from src.core.infra.gating import gates
        from src.core.infra.gating_checks import register_default_gates

        assert gates is not None
        assert callable(register_default_gates)

    def test_notification_queue_import(self) -> None:
        from src.core.scheduling.notification_queue import notification_queue

        assert notification_queue is not None

    def test_context_engine_import(self) -> None:
        from src.core.context.engine import engine

        assert engine is not None

    def test_context_files_import(self) -> None:
        from src.core.memory.context_files import (
            index_contexts_to_fts,
            init_owner_context,
            set_main_loop,
        )

        assert callable(index_contexts_to_fts)
        assert callable(init_owner_context)
        assert callable(set_main_loop)

    def test_hooks_import(self) -> None:
        from src.core.infra.hooks import hooks

        assert hooks is not None

    def test_cache_manager_import(self) -> None:
        from src.core.cache.manager import cache_manager

        assert cache_manager is not None

    def test_vector_store_import(self) -> None:
        from src.core.actions.vector_store import get_vector_store

        assert callable(get_vector_store)

    def test_key_rotation_import(self) -> None:
        from src.core.crypto.key_rotation import init_rotation_manager

        assert callable(init_rotation_manager)

    def test_builtin_tools_import(self) -> None:
        from src.core.actions import register_builtin_tools

        assert callable(register_builtin_tools)

    def test_prefetch_tracker_import(self) -> None:
        from src.core.cache.prefetch import prefetch_tracker

        assert prefetch_tracker is not None

    def test_contact_memory_digest_import(self) -> None:
        from src.core.contacts.contact_memory_digest import get_contact_digest

        assert callable(get_contact_digest)


# ── Shutdown / close-resource imports ─────────────────────────────────────


class TestShutdownChainImports:
    """Imports triggered during graceful shutdown."""

    def test_avito_service_import(self) -> None:
        from src.core.avito.service import shutdown_avito_rotator

        assert callable(shutdown_avito_rotator)

    def test_shutdown_stop_ff_import(self) -> None:
        from src.core.infra.task_manager import stop_ff_tasks

        assert callable(stop_ff_tasks)

    def test_mcp_playwright_close_import(self) -> None:
        from src.core.actions.mcp_playwright import _close_browser

        assert callable(_close_browser)

    def test_close_resource_imports(self) -> None:
        """All modules used in _close_shared_resources() must import."""
        from src.core.actions.embedding_cache import close as _ec_close
        from src.core.actions.mcp_oauth import mcp_oauth
        from src.core.actions.mcp_timer import cancel_all_timers
        from src.core.actions.pubmed_client import close_client
        from src.core.avito.service import _close_stealth_session
        from src.core.intelligence.dsm import close_dsm_db
        from src.core.memory.context_files import close_qdrant
        from src.bot.handlers.avito_cmd import close_avito_cache_db
        from src.db.session import engine
        from src.llm.provider_manager import flush_provider_cache

        assert callable(_ec_close)
        assert mcp_oauth is not None
        assert callable(cancel_all_timers)
        assert callable(close_client)
        assert callable(_close_stealth_session)
        assert callable(close_dsm_db)
        assert callable(close_qdrant)
        assert callable(close_avito_cache_db)
        assert engine is not None
        assert callable(flush_provider_cache)
