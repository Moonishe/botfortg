"""Plugin discovery and loading system.

Discovers plugins from the plugins/ directory using plugin.yaml manifests.
Designed as a fail-safe wrapper: if plugin discovery fails, the system
falls back to the built-in module list in bootstrap.py.

Plugins can implement the TelegramHelperPlugin ABC from plugin_base.py
for automatic lifecycle management (on_activate / on_deactivate).
"""

from __future__ import annotations

import importlib
import inspect
import logging
from pathlib import Path

import yaml

from src.core.actions.plugin_base import TelegramHelperPlugin

logger = logging.getLogger(__name__)


class PluginLoader:
    """Discovers and loads plugins from plugins/ directory."""

    def __init__(self, plugins_dir: str = "plugins") -> None:
        self._plugins_dir = Path(plugins_dir)
        self._loaded: dict[str, str] = {}  # name → module_path
        self._instances: dict[str, TelegramHelperPlugin] = {}  # name → plugin instance

    def discover(self) -> list[str]:
        """Find all plugin directories with plugin.yaml.

        Returns:
            List of directory paths (as strings) containing valid plugin.yaml files.
        """
        if not self._plugins_dir.exists():
            logger.debug(
                "Plugins directory %s does not exist, no plugins discovered",
                self._plugins_dir,
            )
            return []
        discovered: list[str] = []
        try:
            for d in self._plugins_dir.iterdir():
                try:
                    # ponytail: cheap string check first, before fs calls
                    if (
                        not d.name.startswith("_")  # skip _template, __pycache__, etc.
                        and d.is_dir()
                        and not d.is_symlink()
                        and (d / "plugin.yaml").exists()
                    ):
                        discovered.append(str(d))
                except OSError:
                    logger.debug("Skipping inaccessible entry: %s", d, exc_info=True)
        except OSError:
            logger.debug(
                "Plugins directory %s is not readable, no plugins discovered",
                self._plugins_dir,
            )
        if discovered:
            logger.info("Discovered %d plugin(s): %s", len(discovered), discovered)
        return discovered

    def load_plugin(self, plugin_dir: str) -> bool:
        """Load a plugin by importing its module.

        Args:
            plugin_dir: Path to the plugin directory containing plugin.yaml.

        Returns:
            True if the plugin was loaded successfully, False otherwise.
        """
        import re

        yaml_path = Path(plugin_dir) / "plugin.yaml"
        if not yaml_path.exists():
            logger.warning("No plugin.yaml in %s", plugin_dir)
            return False

        try:
            with open(yaml_path, encoding="utf-8") as f:
                manifest = yaml.safe_load(f)
        except Exception:
            logger.exception("Failed to parse plugin.yaml at %s", yaml_path)
            return False

        if not isinstance(manifest, dict):
            logger.warning(
                "plugin.yaml at %s is not a valid dict (got %s)",
                yaml_path,
                type(manifest).__name__,
            )
            return False

        module_path = manifest.get("module")
        if (
            not module_path
            or not isinstance(module_path, str)
            or not module_path.strip()
        ):
            logger.warning(
                "Invalid or missing 'module' field in plugin.yaml at %s (got %r)",
                yaml_path,
                module_path,
            )
            return False

        module_path = module_path.strip()

        # ── Security: validate module_path ─────────────────────────────
        # Only allow modules under the plugins.* namespace to prevent
        # arbitrary system module imports (e.g. ``module: os``,
        # ``module: subprocess``).
        if not module_path.startswith("plugins."):
            logger.error(
                "Rejected plugin module %r: must start with 'plugins.'",
                module_path,
            )
            return False

        # Reject path traversal and shell metacharacters
        if ".." in module_path or "/" in module_path or "\\" in module_path:
            logger.error(
                "Rejected plugin module %r: invalid characters (path traversal)",
                module_path,
            )
            return False

        # Reject non-identifier chars (only allow dots, underscores, alphanumeric)
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.]*", module_path):
            logger.error(
                "Rejected plugin module %r: invalid Python module name",
                module_path,
            )
            return False

        # Verify the module file actually exists under plugins/ directory
        # before importing, to prevent shadowing built-in modules.
        module_path_parts = module_path.split(".")
        # Expected: plugins.<plugin_name> — so len >= 2
        if len(module_path_parts) < 2:
            logger.error(
                "Rejected plugin module %r: too short (expected plugins.<name>)",
                module_path,
            )
            return False

        expected_subdir = self._plugins_dir / module_path_parts[1]
        if not expected_subdir.is_dir():
            logger.warning(
                "Plugin module %r does not correspond to directory %s",
                module_path,
                expected_subdir,
            )
            # Not a hard error — the module might be a single-file plugin

        plugin_name = manifest.get("name", module_path)
        if not isinstance(plugin_name, str):
            plugin_name = module_path
        try:
            importlib.import_module(module_path)
            self._loaded[plugin_name] = module_path
            logger.info("Loaded plugin: %s (module=%s)", plugin_name, module_path)
            return True
        except Exception:
            logger.exception(
                "Failed to load plugin %s (module=%s)", plugin_name, module_path
            )
            return False

    async def activate_plugin(self, plugin_name: str) -> bool:
        """Activate a loaded plugin by calling its on_activate() lifecycle hook.

        Scans the module for TelegramHelperPlugin subclasses, instantiates
        them, and calls on_activate().

        Returns True if activation succeeded, False otherwise.
        """
        if plugin_name not in self._loaded:
            logger.warning("Plugin %r not loaded — cannot activate", plugin_name)
            return False

        module_path = self._loaded[plugin_name]
        try:
            module = importlib.import_module(module_path)
        except Exception:
            logger.exception("Failed to re-import module %s", module_path)
            return False

        # Find all TelegramHelperPlugin subclasses in the module
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, TelegramHelperPlugin) or obj is TelegramHelperPlugin:
                continue
            try:
                instance = obj()
                await instance.on_activate()
                self._instances[plugin_name] = instance
                logger.info("Activated plugin: %s v%s", instance.name, instance.version)
                return True
            except Exception:
                logger.exception("Failed to activate plugin %r", plugin_name)
                return False

        # No plugin class found — module loaded but has no lifecycle
        logger.debug(
            "Plugin %r has no TelegramHelperPlugin subclass — skip activate",
            plugin_name,
        )
        return True  # not an error

    async def deactivate_plugin(self, plugin_name: str) -> bool:
        """Deactivate a plugin by calling its on_deactivate() lifecycle hook.

        Returns True if deactivation succeeded, False otherwise.
        """
        instance = self._instances.pop(plugin_name, None)
        if instance is None:
            return True  # nothing to deactivate

        try:
            await instance.on_deactivate()
            logger.info("Deactivated plugin: %s", instance.name)
            return True
        except Exception:
            logger.exception("Failed to deactivate plugin %r", plugin_name)
            return False

    async def activate_all(self) -> int:
        """Activate all loaded plugins. Returns count of activated plugins."""
        count = 0
        for name in list(self._loaded.keys()):
            if await self.activate_plugin(name):
                count += 1
        return count

    async def deactivate_all(self) -> int:
        """Deactivate all active plugins. Returns count of deactivated plugins."""
        count = 0
        for name in list(self._instances.keys()):
            if await self.deactivate_plugin(name):
                count += 1
        return count

    @property
    def active_plugins(self) -> dict[str, TelegramHelperPlugin]:
        """Return dict of active plugin name → instance."""
        return dict(self._instances)
