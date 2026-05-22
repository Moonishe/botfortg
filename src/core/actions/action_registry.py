"""Action metadata registry for LLM intents.

Handlers still live in bot modules in V1; the registry provides a single source
for schemas, risk levels, and prompt descriptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ActionSpec:
    name: str
    required: set[str] = field(default_factory=set)
    allowed: set[str] = field(default_factory=set)
    risk_level: str = "low"
    description_for_prompt: str = ""


# Universal safe keys preserved even for unknown intents
SAFE_KEYS: set[str] = {
    "intent",
    "contact",
    "contact_id",
    "query",
    "peer_query",
    "text",
    "reply",
    "question",
    "action",
    "topic",
    "hours",
    "fact",
    "sentiment",
    "confidence",
    "mode",
    "limit",
    "contact_name",
}


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, ActionSpec] = {}

    def register(
        self,
        name: str,
        *,
        required: list[str] | None = None,
        allowed: list[str] | None = None,
        risk_level: str = "low",
        description_for_prompt: str = "",
    ) -> None:
        allowed_set = set(allowed or [])
        required_set = set(required or [])
        allowed_set |= required_set | {"intent"}
        self._actions[name] = ActionSpec(
            name=name,
            required=required_set,
            allowed=allowed_set,
            risk_level=risk_level,
            description_for_prompt=description_for_prompt,
        )

    def get(self, name: str | None) -> ActionSpec | None:
        return self._actions.get(name or "")

    def sanitize(self, intent: dict[str, Any]) -> dict[str, Any]:
        spec = self.get(str(intent.get("intent") or ""))
        if spec is None:
            return {k: v for k, v in intent.items() if k in SAFE_KEYS}
        return {k: v for k, v in intent.items() if k in spec.allowed}

    def prompt_descriptions(self) -> str:
        lines = []
        for spec in sorted(self._actions.values(), key=lambda s: s.name):
            if spec.description_for_prompt:
                lines.append(
                    f'- "{spec.name}" ({spec.risk_level}) — {spec.description_for_prompt}'
                )
        return "\n".join(lines)


action_registry = ActionRegistry()


def _register_defaults() -> None:
    r = action_registry
    r.register("chat", allowed=["reply"], description_for_prompt="respond directly")
    r.register("unknown", description_for_prompt="fallback when nothing is understood")
    r.register(
        "clarify",
        required=["question"],
        description_for_prompt="ask a concrete clarification",
    )
    r.register(
        "multi",
        allowed=["actions"],
        risk_level="medium",
        description_for_prompt="run several actions",
    )
    r.register(
        "send_message",
        required=["recipient", "text"],
        risk_level="high",
        description_for_prompt="prepare a message for a contact",
    )
    r.register(
        "summarize_chat",
        required=["contact"],
        risk_level="medium",
        description_for_prompt="summarize chat with contact",
    )
    r.register(
        "tasks_for_chat",
        required=["contact"],
        risk_level="medium",
        description_for_prompt="extract commitments from chat",
    )
    r.register(
        "draft_reply",
        required=["contact"],
        allowed=["contact", "instruction"],
        risk_level="medium",
        description_for_prompt="draft reply for contact",
    )
    r.register(
        "catchup",
        required=["contact"],
        risk_level="medium",
        description_for_prompt="summarize where conversation stopped",
    )
    r.register(
        "search",
        required=["query"],
        allowed=["query", "peer_query", "contact"],
        description_for_prompt="search messages",
    )
    r.register(
        "find_in_chats",
        required=["query"],
        allowed=["query", "action"],
        description_for_prompt="find chats by topic",
    )
    r.register(
        "news_digest",
        required=["topic"],
        allowed=["topic", "hours"],
        description_for_prompt="build news digest",
    )
    r.register("list_todos", description_for_prompt="show open commitments")
    r.register(
        "set_setting",
        required=["key", "value"],
        risk_level="high",
        description_for_prompt="change an allowed setting",
    )
    r.register(
        "add_news_topic",
        required=["topic"],
        allowed=["topic", "hours"],
        risk_level="medium",
        description_for_prompt="add news topic",
    )
    r.register(
        "remove_news_topic",
        required=["topic"],
        risk_level="medium",
        description_for_prompt="remove news topic",
    )
    r.register(
        "add_reminder",
        required=["text"],
        allowed=["text", "when", "peer_query"],
        risk_level="medium",
        description_for_prompt="add reminder",
    )
    r.register(
        "remove_reminder",
        required=["query"],
        risk_level="high",
        description_for_prompt="remove reminder",
    )
    r.register(
        "remove_api_key",
        allowed=["slot_id", "all"],
        risk_level="critical",
        description_for_prompt="remove an API key slot",
    )
    r.register(
        "add_reminders_from_chat",
        required=["contact"],
        risk_level="medium",
        description_for_prompt="extract reminders from chat",
    )
    r.register(
        "add_api_key",
        required=["provider", "key"],
        risk_level="high",
        description_for_prompt="add an API key for a provider",
    )
    r.register(
        "store_memory",
        required=["fact"],
        allowed=["fact", "contact", "sentiment", "confidence"],
        risk_level="medium",
        description_for_prompt="store memory fact",
    )
    r.register(
        "check_memories",
        allowed=["questions"],
        risk_level="medium",
        description_for_prompt="check stale memories",
    )
    r.register(
        "forget_memory",
        required=["query"],
        allowed=["query", "contact", "confirm_multi"],
        risk_level="critical",
        description_for_prompt="forget memory facts",
    )
    r.register(
        "list_memories", allowed=["contact"], description_for_prompt="show memories"
    )
    r.register("list_keys", description_for_prompt="list all stored API keys")
    r.register(
        "extract_memories_from_chat",
        required=["contact"],
        risk_level="medium",
        description_for_prompt="extract memories from chat",
    )
    r.register(
        "change_auto_mode",
        required=["mode"],
        risk_level="high",
        description_for_prompt="change auto reply mode",
    )
    r.register(
        "toggle_api_key",
        required=["slot_id"],
        allowed=["slot_id", "action"],
        risk_level="high",
        description_for_prompt="toggle an API key on/off",
    )
    r.register(
        "set_quiet_hours",
        required=["start", "end"],
        risk_level="high",
        description_for_prompt="set quiet hours",
    )
    r.register("show_inbox", description_for_prompt="show inbox")
    r.register("show_self", description_for_prompt="show self profile")
    r.register("show_digest", description_for_prompt="show digest of recent activity")
    r.register("show_today", description_for_prompt="show today's summary")
    r.register("show_skills", description_for_prompt="show available skills")
    r.register("show_threads", description_for_prompt="show active threads")
    r.register(
        "show_trajectory",
        allowed=["only_errors", "limit"],
        description_for_prompt="show trajectory of actions",
    )
    r.register(
        "show_style",
        allowed=["contact_name"],
        description_for_prompt="show chat style settings",
    )
    r.register("show_profile", description_for_prompt="show user profile")
    r.register(
        "index_chats",
        risk_level="medium",
        description_for_prompt="index chats for search",
    )
    r.register(
        "full_analysis",
        allowed=["folders"],
        risk_level="medium",
        description_for_prompt="run full analysis",
    )


_register_defaults()
