# Actions: action validation, conflict checks, indexing, trajectories, tool registry

from src.core.actions.bootstrap import (
    activate_plugins,
    deactivate_plugins,
    register_builtin_tools,
)
from src.core.actions.tool_registry import (
    ToolActionMetadata,
    ToolActionSpec,
    ToolRegistry,
    ToolSpec,
    tool,
    tool_registry,
)
from src.core.actions.auto_discovery import discover_tools

__all__ = [
    "ToolActionMetadata",
    "ToolActionSpec",
    "ToolRegistry",
    "ToolSpec",
    "activate_plugins",
    "deactivate_plugins",
    "discover_tools",
    "register_builtin_tools",
    "tool",
    "tool_registry",
]
