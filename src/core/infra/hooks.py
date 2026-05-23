import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable

logger = logging.getLogger(__name__)


class HookRegistry:
    def __init__(self):
        self._hooks: dict[str, list[Callable]] = defaultdict(list)

    def on(self, name: str, callback: Callable) -> None:
        """Register a hook callback."""
        self._hooks[name].append(callback)

    async def emit(self, name: str, **kwargs) -> None:
        """Fire all callbacks for a hook point. Each callback runs in try/except."""
        for cb in self._hooks.get(name, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(**kwargs)
                else:
                    cb(**kwargs)
            except Exception:
                logger.exception(
                    "Hook %s callback %s failed", name, getattr(cb, "__name__", cb)
                )


hooks = HookRegistry()
