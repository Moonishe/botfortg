"""Idempotent registration for built-in action tools."""

from __future__ import annotations

import importlib
import logging
import threading

logger = logging.getLogger(__name__)

_BUILTIN_MODULES = (
    "src.core.actions.legacy_builtin_tools",
    "src.core.actions.mcp_tools",
    "src.core.actions.mcp_web",
    "src.core.actions.mcp_connectors",
    "src.core.actions.cross_search_tool",
    "src.core.actions.recall_memory_tool",
    "src.core.actions.search_contexts_tool",
    "src.core.actions.sdd_executor",
    "src.core.actions.mcp_telegram",
    "src.core.actions.mcp_http",
    "src.core.actions.mcp_oauth_tool",
    "src.core.actions.mcp_reminders",
    "src.core.actions.mcp_profile",
    "src.core.actions.session_search_tool",
    "src.core.actions.mcp_shell",
    "src.core.actions.mcp_weather",
    "src.core.actions.mcp_crypto",
    "src.core.actions.mcp_diff",
    "src.core.actions.mcp_hash",
    "src.core.actions.mcp_whois",
    "src.core.actions.mcp_qr",
    "src.core.actions.mcp_json",
    "src.core.actions.mcp_avito",
    "src.core.actions.mcp_avito_watch",
    "src.core.actions.mcp_calculator",
    "src.core.actions.mcp_code_exec",
    "src.core.actions.mcp_codegraph",
    "src.core.actions.mcp_translate",
    "src.core.actions.mcp_timer",
    "src.core.actions.mcp_notify",
    "src.core.actions.mcp_gmail",
    "src.core.actions.mcp_file_analyzer",
    "src.core.actions.mcp_youtube",
    "src.core.actions.mcp_file_send",
    "src.core.actions.mcp_todoist",
    "src.core.actions.mcp_pdf",
    "src.core.actions.mcp_monitor",
    "src.core.actions.mcp_zip",
    "src.core.actions.mcp_git",
    "src.core.actions.mcp_processes",
    "src.core.actions.mcp_network",
    "src.core.actions.mcp_image",
    "src.core.actions.mcp_env",
    "src.core.actions.mcp_logs",
    "src.core.actions.mcp_screenshot",
    "src.core.actions.mcp_rss",
    "src.core.actions.mcp_video",
    "src.core.actions.mcp_playwright",
    "src.core.actions.mcp_excel",
    "src.core.actions.mcp_spellcheck",
    "src.core.actions.mcp_context7",
    "src.core.actions.dsm_memory_tool",
    "src.core.actions.mcp_vision",
    "src.core.actions.mcp_skill",
    "src.core.actions.mcp_skill_pipeline",
    "src.core.actions.mcp_web_search",
    "src.core.actions.mcp_photo_search",
    "src.core.actions.mcp_self_audit",
    "src.core.actions.mcp_search_docs",
    "src.core.actions.mcp_pubmed",
    "src.core.actions.mcp_working_memory",
    "src.core.actions.mcp_memory_edit",
    "src.core.actions.mcp_episodic",
    "src.core.actions.mcp_planner",
    "src.core.actions.mcp_reasoning",
    "src.core.actions.mcp_agent",
    "src.core.actions.mcp_deep_research",
    "src.core.actions.mcp_fact_crud",
    "src.core.actions.mcp_causal",
    "src.core.actions.mcp_knowledge_graph",
    "src.core.actions.mcp_reactions",
    "src.core.actions.mcp_exchange",
)

_BUILTINS_REGISTERED = False
_BUILTINS_LOCK = threading.RLock()


def register_builtin_tools() -> None:
    """Import built-in tool modules once so their decorators register tools.

    Bootstrap must stay side-effect light: importing official tool modules may
    register handlers, but must not perform network, DB, browser, subprocess,
    or filesystem writes.
    """

    global _BUILTINS_REGISTERED
    # Fast-path: avoid lock contention when already registered.
    if _BUILTINS_REGISTERED:
        return None

    with _BUILTINS_LOCK:
        # Re-check under lock to prevent double-registration race.
        if _BUILTINS_REGISTERED:
            return None

        # 1. Try plugin discovery first — if plugins/ directory has plugin.yaml manifests,
        #    import those modules. On any failure, fall back to _BUILTIN_MODULES.
        try:
            from src.core.actions.plugin_loader import PluginLoader

            loader = PluginLoader()
            discovered = loader.discover()
            if discovered:
                logger.info("Found %d plugin(s), loading...", len(discovered))
                for plugin_dir in discovered:
                    loader.load_plugin(plugin_dir)
        except Exception:
            logger.exception("Plugin discovery failed, falling back to built-in list")

        for module_name in _BUILTIN_MODULES:
            try:
                importlib.import_module(module_name)
            except Exception:
                logger.exception(
                    "Failed to import built-in tool module %s", module_name
                )
                # continue — не блокировать остальные инструменты из-за одного сбойного
        _BUILTINS_REGISTERED = True
    return None
