"""Tests for command registry factory/initialization pattern."""

from __future__ import annotations

import pytest

from src.bot.command_registry import (
    CommandRegistry,
    register_all_commands,
    get_registry,
)


class TestCommandRegistry:
    def test_new_registry_is_empty(self) -> None:
        registry = CommandRegistry()
        assert registry.as_telegram_commands() == []
        assert registry.format_help() == "🤖 <b>Доступные команды:</b>\n"

    def test_register_command(self) -> None:
        registry = CommandRegistry()
        registry.register("test", "Test command", "general")
        cmds = registry.as_telegram_commands()
        assert len(cmds) == 1
        assert cmds[0].command == "test"
        assert cmds[0].description == "Test command"

    def test_register_all_commands_returns_populated_registry(self) -> None:
        registry = CommandRegistry()
        returned = register_all_commands(registry)
        assert returned is registry
        assert len(registry.as_telegram_commands()) > 0
        # Admin and diagnostics categories are excluded from the menu.
        assert not any(c.command == "avito" for c in registry.as_telegram_commands())

    def test_register_all_commands_creates_registry_when_none_provided(self) -> None:
        register_all_commands()  # resets global holder
        registry = get_registry()
        assert len(registry.as_telegram_commands()) > 0

    def test_get_registry_before_init_raises(self) -> None:
        import src.bot.command_registry as cr

        original = cr._registry
        try:
            cr._registry = None
            with pytest.raises(
                RuntimeError, match="CommandRegistry has not been initialized"
            ):
                get_registry()
        finally:
            cr._registry = original

    def test_help_uses_get_registry(self) -> None:
        register_all_commands()
        registry = get_registry()
        assert "Доступные команды" in registry.format_help()
