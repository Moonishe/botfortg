import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Event type constants
MEMORY_MUTATED = "memory.mutated"  # add, update, delete
MEMORY_EXTRACTED = "memory.extracted"
EPISODE_COMPLETED = "episode.completed"
USER_MESSAGE_RECEIVED = "user.message.received"
SESSION_RESUMED = "session.resumed"
PREFERENCE_LEARNED = "preference.learned"
INSIGHT_GENERATED = "insight.generated"
AGENT_STARTED = "agent.started"
AGENT_COMPLETED = "agent.completed"
TOOL_EXECUTED = "tool.executed"
EVOLUTION_CHAIN_DETECTED = "evolution_chain.detected"
RESEARCH_COMPLETED = "research.completed"
CIRCUIT_STATE = "circuit.state"


class EventBus:
    """In-memory pub/sub event bus. Single instance per process."""

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, event_type: str, handler: Callable):
        """Subscribe handler to event type. Handler: async callable(event_data)."""
        self._handlers[event_type].append(handler)

    def on(self, event_type: str):
        """Decorator for subscribing."""

        def decorator(fn):
            self.subscribe(event_type, fn)
            return fn

        return decorator

    async def emit(self, event_type: str, **data):
        """Emit event to all subscribers. Non-blocking (fire-and-forget per handler)."""
        handlers = self._handlers.get(event_type, [])
        if not handlers:
            return

        # Fire all handlers in parallel, catch errors individually
        async def _safe_call(handler):
            try:
                await handler(**data)
            except Exception:
                logger.exception("Event handler failed for %s", event_type)

        await asyncio.gather(*[_safe_call(h) for h in handlers], return_exceptions=True)

    def handler_count(self, event_type: str | None = None) -> int:
        """Return number of handlers. If event_type is None, return total."""
        if event_type:
            return len(self._handlers.get(event_type, []))
        return sum(len(h) for h in self._handlers.values())


# Singleton
event_bus = EventBus()
