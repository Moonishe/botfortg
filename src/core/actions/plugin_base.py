"""Plugin ABC — base class for all TelegramHelper plugins.

Usage::

    from src.core.actions.plugin_base import TelegramHelperPlugin

    class MyPlugin(TelegramHelperPlugin):
        name = "my_plugin"
        version = "1.0.0"
        description = "My custom plugin"

        async def on_activate(self) -> None:
            self.hooks.on("on_message_received", self._handle_message)

        async def on_deactivate(self) -> None:
            pass  # cleanup resources

        async def _handle_message(self, **kwargs) -> None:
            ...
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from src.core.infra.hooks import HookRegistry, hooks

logger = logging.getLogger(__name__)


class TelegramHelperPlugin(ABC):
    """Base class for all plugins.

    Subclasses must define ``name``, ``version``, and ``description``.
    Override ``on_activate()`` to subscribe to hooks and initialise resources.
    Override ``on_deactivate()`` to clean up.

    The ``hooks`` attribute provides access to the global HookRegistry
    for subscribing to lifecycle and message events.
    """

    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    category: str = "utility"
    risk: str = "low"

    def __init__(self) -> None:
        self.hooks: HookRegistry = hooks

    @abstractmethod
    async def on_activate(self) -> None:
        """Called after the plugin module is imported.

        Subscribe to hooks, initialise connections, register handlers here.
        """
        ...

    @abstractmethod
    async def on_deactivate(self) -> None:
        """Called before the plugin is unloaded or during shutdown.

        Unsubscribe from hooks, close connections, flush buffers here.
        """
        ...

    def __repr__(self) -> str:
        return f"<Plugin {self.name} v{self.version}>"
