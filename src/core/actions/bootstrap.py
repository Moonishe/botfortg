"""Idempotent registration: core modules + auto-discovery of mcp_*.py tools."""

from __future__ import annotations

import importlib
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.actions.plugin_loader import PluginLoader

logger = logging.getLogger(__name__)

_NON_MCP_MODULES = (
    "src.core.intelligence.error_classifier",
    "src.core.intelligence.iteration_budget",
    "src.core.actions.legacy_builtin_tools",
    "src.core.actions.cross_search_tool",
    "src.core.actions.recall_memory_tool",
    "src.core.actions.search_contexts_tool",
    "src.core.actions.sdd_executor",
    "src.core.actions.session_search_tool",
    "src.core.actions.dsm_memory_tool",
)

_BUILTINS_REGISTERED = False
_BUILTINS_LOCK = threading.RLock()

# ── Plugin loader instance (preserved for lifecycle activation) ──
_plugin_loader: PluginLoader | None = None


def register_builtin_tools(*, force: bool = False) -> None:
    """Import built-in tool modules once so their decorators register tools.

    Bootstrap must stay side-effect light: importing official tool modules may
    register handlers, but must not perform network, DB, browser, subprocess,
    or filesystem writes.

    Args:
        force: When True, reset the registration flag and clear the tool
            registry so that the next call re-runs discovery.
    """

    global _BUILTINS_REGISTERED
    with _BUILTINS_LOCK:
        if force:
            _BUILTINS_REGISTERED = False
            # Clear the registry so that stale tools are not retained.
            from src.core.actions.tool_registry import tool_registry

            tool_registry._tools.clear()

        # Fast-path: avoid re-registration when already registered.
        if _BUILTINS_REGISTERED:
            return None

        # 1. Try plugin discovery first — if plugins/ directory has plugin.yaml
        #    manifests, import those modules. Core and auto-discovered modules follow.
        try:
            from src.core.actions.plugin_loader import PluginLoader

            loader = PluginLoader()
            discovered = loader.discover()
            if discovered:
                logger.info("Found %d plugin(s), loading...", len(discovered))
                for plugin_dir in discovered:
                    loader.load_plugin(plugin_dir)
            # Preserve loader for later lifecycle activation
            global _plugin_loader
            _plugin_loader = loader
        except Exception:
            logger.exception("Plugin discovery failed, falling back to built-in list")

        # Step 1: Import non-MCP core modules
        for module_name in _NON_MCP_MODULES:
            try:
                importlib.import_module(module_name)
            except Exception:
                logger.exception("Failed to import core module %s", module_name)

        # Step 2: Auto-discover all mcp_*.py tools
        try:
            from src.core.actions.auto_discovery import discover_tools

            mcp_count = discover_tools()
            logger.info("Auto-discovered %d MCP tool modules", mcp_count)
        except Exception:
            logger.exception("Auto-discovery of MCP tools failed")
        _BUILTINS_REGISTERED = True
    return None


async def activate_plugins() -> int:
    """Activate lifecycle hooks for all loaded plugins.

    Must be called from an async context (event loop) AFTER
    ``register_builtin_tools()`` has completed.
    Returns count of successfully activated plugins.
    """
    global _plugin_loader
    if _plugin_loader is None:
        return 0
    try:
        return await _plugin_loader.activate_all()
    except Exception:
        logger.exception("Plugin activation failed")
        return 0


async def deactivate_plugins() -> int:
    """Deactivate lifecycle hooks for all active plugins.

    Must be called during graceful shutdown.
    Returns count of successfully deactivated plugins.
    """
    global _plugin_loader
    if _plugin_loader is None:
        return 0
    try:
        return await _plugin_loader.deactivate_all()
    except Exception:
        logger.exception("Plugin deactivation failed")
        return 0
