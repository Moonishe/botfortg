"""Service layer — business logic for handlers, separated from persistence.

Re-exports public functions from sub-modules so handlers can do::

    from src.core.services import add_key, save_memory, get_user_settings
"""

from __future__ import annotations

# ── Exceptions ─────────────────────────────────────────────────────────
from .exceptions import (
    NotFoundError,
    ProviderError,
    ServiceError,
    ValidationError,
)

# ── Key management ─────────────────────────────────────────────────────
from .key_management_service import (
    VALID_PROVIDERS,
    add_key,
    delete_key,
    get_user_keys,
    validate_key,
)

# ── Memory ─────────────────────────────────────────────────────────────
from .memory_service import (
    delete_memory,
    save_memory,
    search_memories,
)

# ── Settings ───────────────────────────────────────────────────────────
from .settings_service import (
    get_user_settings,
    reset_settings,
    update_setting,
)

__all__ = [
    # Exceptions
    "ServiceError",
    "ValidationError",
    "NotFoundError",
    "ProviderError",
    # Key management
    "VALID_PROVIDERS",
    "get_user_keys",
    "add_key",
    "delete_key",
    "validate_key",
    # Memory
    "save_memory",
    "search_memories",
    "delete_memory",
    # Settings
    "get_user_settings",
    "update_setting",
    "reset_settings",
]
