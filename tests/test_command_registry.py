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

    def test_register_all_commands_includes_recent_handlers(self) -> None:
        """Commands added in recent handlers must be present in the registry."""
        register_all_commands()
        registry = get_registry()
        commands = {
            c.command for c in registry.as_telegram_commands(include_admin=True)
        }
        for name in (
            "ask",
            "catchup",
            "style",
            "mode",
            "analyze",
            "docs",
            "explain",
            "humanize",
            "news",
            "news_channels",
            "news_topics",
            "pubmed",
            "pubmed_abstract",
            "pubmed_full",
            "wiki",
            "sessions",
            "trajectory",
            "evolve",
            "ops",
        ):
            assert name in commands, f"/{name} missing from registry"
        # Admin commands are excluded from the menu but present in the registry.
        admin_commands = registry.by_category().get("admin", [])
        admin_names = {c.name for c in admin_commands}
        assert {"approve", "revoke", "pending"}.issubset(admin_names)
