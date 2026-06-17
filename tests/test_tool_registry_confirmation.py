from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.core.actions import register_builtin_tools
from src.core.actions.mcp_processes import mcp_processes
from src.core.actions.tool_registry import (
    ToolActionSpec,
    ToolRegistry,
    ToolSpec,
    tool_registry,
)
from src.core.actions.recall_memory_tool import recall_memory

# Fixture: ensure approval_mode is "smart" during confirmation tests.
# Without this, CONFIRMATION_TESTS break when APPROVAL_MODE=off in .env
# because ToolRegistry.execute() reads settings.approval_mode at runtime.
_CONFIRMATION_TESTS_PATCH = patch(
    "src.config.settings.approval_mode", "smart", create=True
)


@pytest.mark.asyncio
async def test_execute_blocks_unconfirmed_tool() -> None:
    calls = 0

    async def handler() -> dict:
        nonlocal calls
        calls += 1
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="dangerous",
            description="Dangerous test tool",
            category="test",
            handler=handler,
            risk="high",
            requires_confirmation=True,
        )
    )

    with _CONFIRMATION_TESTS_PATCH:
        result = await registry.execute("dangerous")

    assert result["error"] == "requires confirmation"
    assert result["approval_mode"] == "smart"
    assert calls == 0


@pytest.mark.asyncio
async def test_execute_runs_confirmed_tool() -> None:
    calls = 0

    async def handler() -> dict:
        nonlocal calls
        calls += 1
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="dangerous",
            description="Dangerous test tool",
            category="test",
            handler=handler,
            risk="high",
            requires_confirmation=True,
        )
    )

    result = await registry.execute("dangerous", _confirmed=True)

    assert result == {"ok": True}
    assert calls == 1


@pytest.mark.asyncio
async def test_recall_memory_error_matches_schema() -> None:
    result = await recall_memory("любой запрос")

    assert result["ok"] is False
    assert result["facts"] == []
    assert result["found"] == 0
    assert "error" in result


@pytest.mark.asyncio
async def test_execute_blocks_high_risk_tool_without_confirmation_flag() -> None:
    calls = 0

    async def handler() -> dict:
        nonlocal calls
        calls += 1
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="dangerous_by_risk",
            description="Dangerous test tool",
            category="test",
            handler=handler,
            risk="high",
            requires_confirmation=False,
        )
    )

    with _CONFIRMATION_TESTS_PATCH:
        result = await registry.execute("dangerous_by_risk")

    assert result["error"] == "requires confirmation"
    assert result["approval_mode"] == "smart"
    assert calls == 0


def test_register_builtin_tools_is_idempotent() -> None:
    register_builtin_tools()
    before = sorted(
        spec.name
        for specs in tool_registry.list_by_category().values()
        for spec in specs
    )

    register_builtin_tools()
    after = sorted(
        spec.name
        for specs in tool_registry.list_by_category().values()
        for spec in specs
    )

    assert after == before


@pytest.mark.asyncio
async def test_execute_uses_action_level_confirmation_metadata() -> None:
    calls: list[str] = []

    async def handler(action: str) -> dict:
        calls.append(action)
        return {"ok": True, "action": action}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="multi",
            description="Multi action test tool",
            category="test",
            handler=handler,
            risk="medium",
            requires_confirmation=False,
            actions={
                "read": ToolActionSpec(name="read", risk="low", read_only=True),
                "write": ToolActionSpec(
                    name="write",
                    risk="high",
                    read_only=False,
                    requires_confirmation=True,
                ),
            },
        )
    )

    with _CONFIRMATION_TESTS_PATCH:
        blocked = await registry.execute("multi", action="write")
    read = await registry.execute("multi", action="read")
    with _CONFIRMATION_TESTS_PATCH:
        write = await registry.execute("multi", action="write", _confirmed=True)

    assert blocked["error"] == "requires confirmation"
    assert blocked["approval_mode"] == "smart"
    assert read == {"ok": True, "action": "read"}
    assert write == {"ok": True, "action": "write"}
    assert calls == ["read", "write"]


@pytest.mark.asyncio
async def test_process_kill_requires_direct_call_confirmation() -> None:
    result = await mcp_processes("kill", pid=1)

    assert result["requires_confirmation"] is True
    assert result["action"] == "kill"


# ══════════════════════════════════════════════════════════════════════════
# TR-1 & TR-2: _redact_params — masks all _REDACT_KEYS, case insensitive
# ══════════════════════════════════════════════════════════════════════════


def test_redact_params_masks_all_redact_keys() -> None:
    """TR-1: _redact_params masks all keys in _REDACT_KEYS."""
    from src.core.actions.tool_registry import _redact_params, _REDACT_KEYS

    # Build params dict with every redact key
    params: dict[str, Any] = {key: f"secret-{key}" for key in _REDACT_KEYS}
    # Also add non-sensitive keys
    params["username"] = "john"
    params["query"] = "search term"
    params["limit"] = 10

    result = _redact_params(params)

    # All sensitive keys should be redacted
    for key in _REDACT_KEYS:
        assert result[key] == "<REDACTED>", f"Key {key!r} was not redacted"

    # Non-sensitive keys should pass through unchanged
    assert result["username"] == "john"
    assert result["query"] == "search term"
    assert result["limit"] == 10


def test_redact_params_case_insensitive() -> None:
    """TR-2: _redact_params is case insensitive."""
    from src.core.actions.tool_registry import _redact_params

    params = {
        "Api_Key": "upperfirst",
        "API_KEY": "allcaps",
        "Token": "titlecase",
        "TOKEN": "shout",
        "Password": "titlepass",
    }
    result = _redact_params(params)

    assert result["Api_Key"] == "<REDACTED>"
    assert result["API_KEY"] == "<REDACTED>"
    assert result["Token"] == "<REDACTED>"
    assert result["TOKEN"] == "<REDACTED>"
    assert result["Password"] == "<REDACTED>"


def test_redact_params_non_sensitive_unchanged() -> None:
    """TR-2b: non-sensitive keys pass through _redact_params unchanged."""
    from src.core.actions.tool_registry import _redact_params

    params = {
        "name": "Alice",
        "age": 30,
        "query": "hello world",
        "action": "read",
        "category": "test",
        "limit": 5,
    }
    result = _redact_params(params)

    assert result == params  # No redaction should occur


def test_redact_params_empty_dict() -> None:
    """TR-2c: _redact_params handles empty dict."""
    from src.core.actions.tool_registry import _redact_params

    result = _redact_params({})
    assert result == {}


# ══════════════════════════════════════════════════════════════════════════
# TR-3: _handler_accepts_kwarg unit
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_handler_accepts_kwarg_variadic() -> None:
    """TR-3: _handler_accepts_kwarg returns True for **kwargs handler."""

    async def variadic_handler(**kwargs: object) -> dict:
        return {"ok": True}

    from src.core.actions.tool_registry import _handler_accepts_kwarg

    assert _handler_accepts_kwarg(variadic_handler, "anything") is True
    assert _handler_accepts_kwarg(variadic_handler, "_confirmed") is True


@pytest.mark.asyncio
async def test_handler_accepts_kwarg_named_match() -> None:
    """TR-3b: _handler_accepts_kwarg returns True for named parameter match."""

    async def named_handler(token: str, query: str) -> dict:
        return {"ok": True, "token": token, "query": query}

    from src.core.actions.tool_registry import _handler_accepts_kwarg

    assert _handler_accepts_kwarg(named_handler, "token") is True
    assert _handler_accepts_kwarg(named_handler, "query") is True
    assert _handler_accepts_kwarg(named_handler, "nonexistent") is False


@pytest.mark.asyncio
async def test_handler_accepts_kwarg_no_match() -> None:
    """TR-3c: _handler_accepts_kwarg returns False for no match."""

    async def strict_handler(a: str, b: int) -> dict:
        return {"a": a, "b": b}

    from src.core.actions.tool_registry import _handler_accepts_kwarg

    assert _handler_accepts_kwarg(strict_handler, "c") is False
    assert _handler_accepts_kwarg(strict_handler, "_confirmed") is False


# ══════════════════════════════════════════════════════════════════════════
# TR-4 & TR-5: execute() error cases
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_execute_nonexistent_tool() -> None:
    """TR-4: execute() with non-existent tool returns error dict."""
    registry = ToolRegistry()

    result = await registry.execute("nonexistent_tool_xyz")
    assert result == {"error": "Tool 'nonexistent_tool_xyz' not found"}


@pytest.mark.asyncio
async def test_execute_handler_exception() -> None:
    """TR-5: execute() handler exception returns error dict."""

    async def failing_handler() -> dict:
        raise RuntimeError("Intentional failure")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="will_fail",
            description="Always fails",
            category="test",
            handler=failing_handler,
            risk="low",
        )
    )

    result = await registry.execute("will_fail")
    assert "error" in result
    assert "will_fail" in result["error"] or "Intentional" in str(result)


# ══════════════════════════════════════════════════════════════════════════
# TR-9: register() duplicate overwrite
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_register_duplicate_overwrite() -> None:
    """TR-9: register() overwrites existing tool with same name."""

    async def handler_v1() -> dict:
        return {"version": 1}

    async def handler_v2() -> dict:
        return {"version": 2}

    registry = ToolRegistry()

    spec1 = ToolSpec(
        name="overwrite_test",
        description="Version 1",
        category="test",
        handler=handler_v1,
        risk="low",
    )
    spec2 = ToolSpec(
        name="overwrite_test",
        description="Version 2",
        category="test",
        handler=handler_v2,
        risk="medium",
    )

    registry.register(spec1)
    assert registry.get("overwrite_test") is spec1

    # Register duplicate — should overwrite
    registry.register(spec2)
    assert registry.get("overwrite_test") is spec2
    assert registry.get("overwrite_test").description == "Version 2"  # type: ignore[union-attr]
    assert registry.get("overwrite_test").risk == "medium"  # type: ignore[union-attr]

    # Verify handler is the new one (non-None returns passed through as-is)
    result = await registry.execute("overwrite_test")
    assert result == {"version": 2}


# ══════════════════════════════════════════════════════════════════════════
# TR-11: get() non-existent → None
# ══════════════════════════════════════════════════════════════════════════


def test_get_nonexistent_returns_none() -> None:
    """TR-11: get() returns None for non-existent tool name."""
    registry = ToolRegistry()

    assert registry.get("does_not_exist") is None
    assert registry.get("") is None
    assert registry.get("also_not_here_123") is None


def test_get_existing_returns_spec() -> None:
    """TR-11b: get() returns ToolSpec for registered tool."""

    async def test_handler() -> dict:
        return {"ok": True}

    registry = ToolRegistry()
    spec = ToolSpec(
        name="test_tool",
        description="A test tool",
        category="test",
        handler=test_handler,
    )
    registry.register(spec)

    result = registry.get("test_tool")
    assert result is spec
    assert result.name == "test_tool"
    assert result.description == "A test tool"


# ══════════════════════════════════════════════════════════════════════════
# EXTRA: ToolSpec dataclass and ToolRegistry lookup edge cases
# ══════════════════════════════════════════════════════════════════════════


def test_toolspec_frozen() -> None:
    """ToolSpec is frozen (immutable)."""
    spec = ToolSpec(
        name="test",
        description="desc",
        category="cat",
        handler=AsyncMock(),
    )
    with pytest.raises(Exception):
        spec.name = "changed"  # type: ignore[misc]


def test_tool_registry_is_available_nonexistent() -> None:
    """is_available returns False for non-existent tool."""
    registry = ToolRegistry()
    assert registry.is_available("nope") is False

    # Also test with tool_registry singleton
    assert tool_registry.is_available("does_not_exist_xyz") is False


@pytest.mark.asyncio
async def test_execute_low_risk_no_confirmation_needed() -> None:
    """Low-risk tools execute without _confirmed flag."""

    async def safe_handler() -> dict:
        return {"ok": True, "safe": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="safe_tool",
            description="A safe tool",
            category="test",
            handler=safe_handler,
            risk="low",
            requires_confirmation=False,
        )
    )

    result = await registry.execute("safe_tool")
    assert result == {"ok": True, "safe": True}


@pytest.mark.asyncio
async def test_execute_critical_risk_blocked() -> None:
    """Critical risk tools require confirmation even without explicit flag."""

    async def critical_handler() -> dict:
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="nuke",
            description="Destructive operation",
            category="test",
            handler=critical_handler,
            risk="critical",
            requires_confirmation=False,  # no explicit flag, but risk is critical
        )
    )

    with _CONFIRMATION_TESTS_PATCH:
        result = await registry.execute("nuke")
    assert result["error"] == "requires confirmation"
    assert result["approval_mode"] == "smart"
