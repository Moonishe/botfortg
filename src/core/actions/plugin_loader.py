"""Plugin discovery and loading system.

Discovers plugins from the plugins/ directory using plugin.yaml manifests.
Designed as a fail-safe wrapper: if plugin discovery fails, the system
falls back to the built-in module list in bootstrap.py.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class PluginLoader:
    """Discovers and loads plugins from plugins/ directory."""

    def __init__(self, plugins_dir: str = "plugins") -> None:
        self._plugins_dir = Path(plugins_dir)
        self._loaded: dict[str, str] = {}  # name → module_path

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
                    if (
                        d.is_dir()
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
        yaml_path = Path(plugin_dir) / "plugin.yaml"
        if not yaml_path.exists():
            logger.warning("No plugin.yaml in %s", plugin_dir)
            return False

        try:
            with open(yaml_path, encoding="utf-8") as f:
                manifest = yaml.safe_load(
                    f
                )  # yaml.safe_load returns Any (None, dict, list, str, ...)
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

    def list_plugins(self) -> list[dict[str, Any]]:
        """List discovered plugins with metadata.

        Returns:
            List of dictionaries with plugin metadata (name, version, description,
            category, loaded status).
        """
        plugins: list[dict[str, Any]] = []
        for plugin_dir in self.discover():
            yaml_path = Path(plugin_dir) / "plugin.yaml"
            try:
                with open(yaml_path, encoding="utf-8") as f:
                    manifest = yaml.safe_load(f)  # yaml.safe_load returns Any
            except Exception:
                logger.warning("Could not read plugin.yaml at %s", yaml_path)
                continue
            if not isinstance(manifest, dict):
                continue
            module_path = manifest.get("module", "")
            plugin_name = manifest.get("name", module_path)
            if not isinstance(plugin_name, str):
                plugin_name = module_path
            plugins.append(
                {
                    "name": manifest.get("name", "unknown"),
                    "version": manifest.get("version", "0.0.0"),
                    "description": manifest.get("description", ""),
                    "category": manifest.get("category", "uncategorized"),
                    "loaded": plugin_name in self._loaded,
                }
            )
        return plugins

    @property
    def loaded_plugins(self) -> dict[str, str]:
        """Return dict of loaded plugin name → module_path."""
        return dict(self._loaded)
