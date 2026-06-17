"""Tests for Tool Auto-Discovery system.

Covers:
- ToolSpec.check_fn / requires_env fields
- ToolRegistry.get_available_tools(), is_available()
- available_only parameter on list_by_category(), search(), list_for_prompt(),
  format_tools_for_task(), format_tools_with_schemas()
- @tool decorator check_fn support
- discover_tools() auto-discovery function
- Backward compatibility (calls without available_only work as before)
"""

from __future__ import annotations

import pytest

from src.core.actions.auto_discovery import discover_tools
from src.core.actions.tool_registry import ToolRegistry, ToolSpec, tool


# ── Helpers ──────────────────────────────────────────────────────────────


async def _noop_handler(**kwargs: object) -> dict[str, bool]:
    return {"ok": True}


# ── Test 1: ToolSpec.check_fn field ──────────────────────────────────────


class TestToolSpecCheckFn:
    def test_check_fn_none_by_default(self) -> None:
        spec = ToolSpec(
            name="no_check",
            description="No check function",
            category="test",
            handler=_noop_handler,
        )
        assert spec.check_fn is None

    def test_check_fn_set(self) -> None:
        spec = ToolSpec(
            name="with_check",
            description="Has check function",
            category="test",
            handler=_noop_handler,
            check_fn=lambda: True,
        )
        assert spec.check_fn is not None
        assert spec.check_fn() is True


# ── Test 2: ToolSpec.requires_env field ──────────────────────────────────


class TestToolSpecRequiresEnv:
    def test_requires_env_empty_by_default(self) -> None:
        spec = ToolSpec(
            name="no_env",
            description="No env required",
            category="test",
            handler=_noop_handler,
        )
        assert spec.requires_env == []

    def test_requires_env_populated(self) -> None:
        spec = ToolSpec(
            name="with_env",
            description="Requires API key",
            category="test",
            handler=_noop_handler,
            requires_env=["API_KEY"],
        )
        assert spec.requires_env == ["API_KEY"]


# ── Test 3: get_available_tools (all available) ──────────────────────────


class TestGetAvailableToolsAllAvailable:
    def test_all_tools_available_without_check_fn(self) -> None:
        registry = ToolRegistry()

        names = ["tool_a", "tool_b", "tool_c"]
        for n in names:
            registry.register(
                ToolSpec(
                    name=n,
                    description=f"Tool {n}",
                    category="test",
                    handler=_noop_handler,
                )
            )

        available = registry.get_available_tools()
        assert len(available) == 3
        result_names = {s.name for s in available}
        assert result_names == set(names)

    def test_empty_registry_returns_empty_list(self) -> None:
        """get_available_tools() при пустом реестре → []."""
        registry = ToolRegistry()
        available = registry.get_available_tools()
        assert available == []
        assert isinstance(available, list)


# ── Test 4: get_available_tools filters by check_fn ──────────────────────


class TestGetAvailableToolsCheckFnFilters:
    def test_filters_disabled_tools(self) -> None:
        registry = ToolRegistry()

        registry.register(
            ToolSpec(
                name="always",
                description="Always available",
                category="test",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="disabled",
                description="Disabled by check_fn",
                category="test",
                handler=_noop_handler,
                check_fn=lambda: False,
            )
        )
        registry.register(
            ToolSpec(
                name="explicit_yes",
                description="Explicitly available",
                category="test",
                handler=_noop_handler,
                check_fn=lambda: True,
            )
        )

        available = registry.get_available_tools()
        assert len(available) == 2
        names = {s.name for s in available}
        assert "always" in names
        assert "explicit_yes" in names
        assert "disabled" not in names


# ── Test 5: is_available ─────────────────────────────────────────────────


class TestIsAvailable:
    def test_nonexistent_tool_returns_false(self) -> None:
        registry = ToolRegistry()
        assert registry.is_available("nonexistent") is False

    def test_tool_without_check_fn_returns_true(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="available",
                description="Available tool",
                category="test",
                handler=_noop_handler,
            )
        )
        assert registry.is_available("available") is True

    def test_tool_with_check_fn_false_returns_false(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="disabled",
                description="Disabled tool",
                category="test",
                handler=_noop_handler,
                check_fn=lambda: False,
            )
        )
        assert registry.is_available("disabled") is False

    def test_tool_with_check_fn_true_returns_true(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="enabled",
                description="Enabled tool",
                category="test",
                handler=_noop_handler,
                check_fn=lambda: True,
            )
        )
        assert registry.is_available("enabled") is True

    def test_check_fn_raising_exception_treats_as_unavailable(self) -> None:
        registry = ToolRegistry()

        def _raise_check() -> bool:
            raise RuntimeError("check_fn failure")

        registry.register(
            ToolSpec(
                name="broken",
                description="Broken check_fn",
                category="test",
                handler=_noop_handler,
                check_fn=_raise_check,
            )
        )
        assert registry.is_available("broken") is False

    def test_check_fn_returning_none_treated_as_unavailable(self) -> None:
        """check_fn() -> None (non-bool) — должен трактоваться как False."""
        registry = ToolRegistry()

        registry.register(
            ToolSpec(
                name="none_return",
                description="check_fn returns None",
                category="test",
                handler=_noop_handler,
                check_fn=lambda: None,  # type: ignore[arg-type]  # intentionally non-bool
            )
        )
        # is_available() must return bool (not None)
        result = registry.is_available("none_return")
        assert result is False  # not None!
        assert isinstance(result, bool)

        # get_available_tools() must exclude it
        available = registry.get_available_tools()
        names = {s.name for s in available}
        assert "none_return" not in names


# ── Test 6: list_by_category available_only ──────────────────────────────


class TestListByCategoryAvailableOnly:
    def test_available_only_excludes_disabled(self) -> None:
        registry = ToolRegistry()

        registry.register(
            ToolSpec(
                name="chat_enabled",
                description="Enabled chat tool",
                category="chat",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="chat_disabled",
                description="Disabled chat tool",
                category="chat",
                handler=_noop_handler,
                check_fn=lambda: False,
            )
        )
        registry.register(
            ToolSpec(
                name="search_tool",
                description="Search tool",
                category="search",
                handler=_noop_handler,
            )
        )

        all_cats = registry.list_by_category(available_only=False)
        assert "chat" in all_cats
        assert len(all_cats["chat"]) == 2  # both tools

        avail_cats = registry.list_by_category(available_only=True)
        assert "chat" in avail_cats
        chat_names = {s.name for s in avail_cats["chat"]}
        assert chat_names == {"chat_enabled"}
        assert "disabled" not in chat_names
        assert "search" in avail_cats  # search category still present


# ── Test 7: search available_only ────────────────────────────────────────


class TestSearchAvailableOnly:
    def test_search_without_filter_includes_all(self) -> None:
        registry = ToolRegistry()

        registry.register(
            ToolSpec(
                name="search_avail",
                description="Available search tool for testing",
                category="test",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="search_disabled",
                description="Disabled search tool for testing",
                category="test",
                handler=_noop_handler,
                check_fn=lambda: False,
            )
        )

        results = registry.search("search", available_only=False)
        names = {s.name for s in results}
        # Both tools should appear since available_only=False
        assert "search_avail" in names
        assert "search_disabled" in names

    def test_search_available_only_filters(self) -> None:
        registry = ToolRegistry()

        registry.register(
            ToolSpec(
                name="fizz",
                description="Fizz tool description",
                category="test",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="buzz",
                description="Buzz tool but disabled",
                category="test",
                handler=_noop_handler,
                check_fn=lambda: False,
            )
        )

        results = registry.search("fizz", available_only=True)
        names = {s.name for s in results}
        assert "fizz" in names
        assert "buzz" not in names


# ── Test 8: @tool decorator with check_fn ────────────────────────────────


@pytest.mark.asyncio
async def test_tool_decorator_check_fn() -> None:
    """Проверить: @tool, где check_fn возвращает False, регистрирует
    инструмент, но is_available → False."""

    # Use a unique name to avoid collisions with other tests
    unique_name = "test_check_decorator_unique_name_2025"

    @tool(
        name=unique_name,
        description="Tool registered via decorator with check_fn=False",
        category="test",
        check_fn=lambda: False,
    )
    async def _test_check_handler(**kwargs: object) -> dict[str, str]:
        return {"ok": "true"}

    # Import the global registry to verify
    from src.core.actions.tool_registry import tool_registry

    # Tool should be registered
    spec = tool_registry.get(unique_name)
    assert spec is not None, f"Tool {unique_name!r} should be registered"
    assert spec.name == unique_name
    assert spec.check_fn is not None

    # But is_available should return False
    assert tool_registry.is_available(unique_name) is False

    # get_available_tools() should exclude it
    avail = tool_registry.get_available_tools()
    avail_names = {s.name for s in avail}
    assert unique_name not in avail_names


# ── Test 9: discover_tools() function ────────────────────────────────────


def test_discover_tools_function() -> None:
    """Вызвать discover_tools() — должен вернуть int > 0."""
    count = discover_tools()
    assert isinstance(count, int)
    assert count > 0, f"Expected > 0 discovered modules, got {count}"

    # mcp_expose is excluded, so count == len(mcp_*.py) - 1
    from pathlib import Path

    package_dir = Path(__file__).parent.parent / "src" / "core" / "actions"
    total_mcp = len(list(package_dir.glob("mcp_*.py")))
    # mcp_expose is excluded
    expected_max = total_mcp - 1
    assert count <= expected_max, (
        f"Loaded {count} modules, but max expected is {expected_max}"
        f" (total mcp_*.py={total_mcp}, minus 1 excluded)"
    )


# ── Test 10: Backward compatibility ─────────────────────────────────────


class TestBackwardCompatibility:
    """Вызов методов БЕЗ available_only должен работать как раньше."""

    def test_list_by_category_default(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="btool_1",
                description="Backward compat tool 1",
                category="btest",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="btool_2",
                description="Backward compat tool 2",
                category="btest",
                handler=_noop_handler,
                check_fn=lambda: False,  # even disabled should appear
            )
        )

        cats = registry.list_by_category()  # default: available_only=False
        assert "btest" in cats
        assert len(cats["btest"]) == 2  # includes disabled

    def test_list_for_prompt_default(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="prompt_tool",
                description="Tool for prompt formatting",
                category="ptest",
                handler=_noop_handler,
            )
        )

        result = registry.list_for_prompt()  # default: available_only=False
        assert isinstance(result, str)
        assert "prompt_tool" in result
        assert "## ptest" in result

    def test_search_default(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="search_backward",
                description="Backward compatible search test",
                category="test",
                handler=_noop_handler,
            )
        )

        results = registry.search("backward")  # default: available_only=False
        assert isinstance(results, list)
        assert len(results) >= 1

    def test_format_tools_for_task_default(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="task_tool",
                description="Tool for task formatting",
                category="tasktest",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_for_task(
            "поиск"
        )  # default: available_only=False
        assert isinstance(result, str)
        # Should include memory category (always added) and potentially search
        assert "memory" in result.lower() or "tasktest" in result.lower()

    def test_format_tools_with_schemas_default(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="schema_tool",
                description="Tool for schema formatting",
                category="stest",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_with_schemas()  # default: available_only=False
        assert isinstance(result, str)
        assert "schema_tool" in result

    def test_format_tools_for_task_available_only(self) -> None:
        """available_only=True excludes tools whose check_fn fails."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="available_tool",
                description="Available tool",
                category="search",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="unavailable_tool",
                description="Unavailable tool",
                category="search",
                handler=_noop_handler,
                check_fn=lambda: False,
            )
        )

        # "weather" maps to search category
        result = registry.format_tools_for_task("weather", available_only=True)
        assert "available_tool" in result
        assert "unavailable_tool" not in result

    def test_format_tools_for_task_no_substring_false_positives(self) -> None:
        """Keyword matching uses word stems, not raw substrings."""
        registry = ToolRegistry()

        # "digital" should not match the 'git' stem in system category.
        assert "system" not in registry._infer_categories("digital camera")
        # "dialog" should not match the 'log' stem in system category.
        assert "system" not in registry._infer_categories("open dialog")
        # "airplane" should not match the 'plan' stem in productivity.
        assert "productivity" not in registry._infer_categories("airplane mode")

    def test_format_tools_for_task_sanitizes_header(self) -> None:
        """Raw user text is sanitized before being embedded in the prompt header."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="task_tool",
                description="Tool for task formatting",
                category="search",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_for_task("поиск `ignore me` #system")
        # Backticks and # should be removed/sanitized
        assert "`ignore me`" not in result
        assert "#system" not in result
        assert "поиск" in result

    def test_format_tools_for_task_scheduling_category(self) -> None:
        """Scheduling category is reachable via cron keywords."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="cron_tool",
                description="Cron tool",
                category="scheduling",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_for_task("создай cron задачу")
        assert "scheduling" in result.lower()
        assert "cron_tool" in result


# ── Test 17: route-specific toolset profiles ─────────────────────────────


class TestRouteProfiles:
    def test_format_tools_for_route_uses_profile_categories(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="memory_tool",
                description="Memory tool",
                category="memory",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="send_message",
                description="Send a message",
                category="messaging",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="code_exec",
                description="Run code",
                category="system",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_for_route(
            "daily report", route="cron_headless", available_only=True
        )
        assert "memory_tool" in result
        assert "send_message" not in result
        assert "code_exec" not in result
        assert "Route profile: cron_headless" in result

    def test_format_tools_for_task_respects_route_profile(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="memory_tool",
                description="Memory tool",
                category="memory",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="send_message",
                description="Send a message",
                category="messaging",
                handler=_noop_handler,
            )
        )

        # With route="cron_headless" messaging tools are excluded even if the
        # task text would normally include them.
        result = registry.format_tools_for_task(
            "send daily report", route="cron_headless", available_only=True
        )
        assert "memory_tool" in result
        assert "send_message" not in result

    def test_format_tools_with_schemas_respects_route(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="send_message",
                description="Send a message",
                category="messaging",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="recall_memory",
                description="Recall memory",
                category="memory",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_with_schemas(route="cron_headless")
        assert "recall_memory" in result
        assert "send_message" not in result

    def test_unknown_route_falls_back_to_all_categories(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="tool",
                description="Tool",
                category="search",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_for_route(
            "search", route="nonexistent", available_only=True
        )
        assert "tool" in result

    def test_format_tools_for_route_empty_task_context(self) -> None:
        """format_tools_for_route with empty task_context falls back to memory."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="memory_tool",
                description="Memory tool",
                category="memory",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_for_route("", route="nonexistent")
        assert "memory" in result.lower()
        assert "memory_tool" in result

    def test_format_tools_for_route_no_route_categories_produces_guard(self) -> None:
        """When route profile has empty categories, guard message appears."""
        registry = ToolRegistry()

        # Temporarily inject a profile with empty categories
        registry.TOOLSET_PROFILES["empty_test"] = {"categories": []}
        try:
            result = registry.format_tools_for_route("test", route="empty_test")
            assert "No tools available" in result
            assert "empty_test" in result
        finally:
            del registry.TOOLSET_PROFILES["empty_test"]

    def test_exclude_names_for_nonexistent_tools_is_harmless(self) -> None:
        """Exclude_names containing non-existent tools shouldn't crash."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="real_tool",
                description="A real tool",
                category="memory",
                handler=_noop_handler,
            )
        )

        # cron_headless has exclude_names with "send_message", "code_exec", "mcp_telegram"
        result = registry.format_tools_with_schemas(route="cron_headless")
        assert "real_tool" in result
        # No crash, no error

    def test_categories_for_profile_empty_returns_none(self) -> None:
        """_categories_for_profile returns None for empty categories set."""
        registry = ToolRegistry()

        # Test known profile with categories
        result = registry._categories_for_profile("cron_headless")
        assert result is not None
        assert len(result) > 0

        # Test unknown route
        result = registry._categories_for_profile("nonexistent_xyz")
        assert result is None

        # Test default_chat with include_all
        result = registry._categories_for_profile("default_chat")
        assert result is None

        # Test route=None
        result = registry._categories_for_profile(None)
        assert result is None

        # Test route=""
        result = registry._categories_for_profile("")
        assert result is None

    def test_excluded_names_for_profile_empty_route(self) -> None:
        """_excluded_names_for_profile returns empty set for empty/None route."""
        registry = ToolRegistry()
        assert registry._excluded_names_for_profile(None) == set()
        assert registry._excluded_names_for_profile("") == set()

    def test_excluded_names_for_profile_nonexistent_route(self) -> None:
        """_excluded_names_for_profile returns empty set for unknown route."""
        registry = ToolRegistry()
        assert registry._excluded_names_for_profile("nonexistent_xyz") == set()

    def test_format_tools_for_task_route_none_backward_compat(self) -> None:
        """format_tools_for_task with route=None works (backward compat)."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="search_tool",
                description="Search tool",
                category="search",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_for_task("поиск", route=None)
        assert "search_tool" in result

    def test_format_tools_with_schemas_route_none_backward_compat(self) -> None:
        """format_tools_with_schemas with route=None works (backward compat)."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="test_tool",
                description="Test",
                category="test",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_with_schemas(route=None)
        assert "test_tool" in result

    def test_format_tools_for_route_empty_categories_after_inference(self) -> None:
        """When _categories_for_profile returns None AND _infer_categories
        returns empty set, 'memory' is added and tools display correctly."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="memory_tool",
                description="Memory tool",
                category="memory",
                handler=_noop_handler,
            )
        )

        # Empty task_context + unknown route → no inferred categories → memory added
        result = registry.format_tools_for_route("", route="nonexistent_xyz")
        assert "memory_tool" in result
        assert "memory" in result.lower()

    def test_format_tools_for_route_only_excluded_tools_in_category(self) -> None:
        """When a category's only tools are all excluded, the category
        header is not emitted (no empty sections)."""
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="send_message",
                description="Send message",
                category="messaging",
                handler=_noop_handler,
            )
        )
        registry.register(
            ToolSpec(
                name="memory_tool",
                description="Memory tool",
                category="memory",
                handler=_noop_handler,
            )
        )

        result = registry.format_tools_for_route("test", route="cron_headless")
        assert "memory_tool" in result
        # send_message is in cron_headless exclude_names — should NOT appear
        assert "send_message" not in result
        # And the messaging category section should not appear at all
        # because all its tools were filtered out
        assert "## messaging" not in result

    def test_sanitize_task_context_edge_cases(self) -> None:
        """_sanitize_task_context handles edge cases: empty, long, special chars."""
        registry = ToolRegistry()
        assert registry._sanitize_task_context("") == ""
        assert registry._sanitize_task_context("   ") == ""
        # Long text truncated to 120
        long_text = "A" * 200
        assert len(registry._sanitize_task_context(long_text)) == 120
        # Newlines collapsed
        result = registry._sanitize_task_context("hello\n\n\nworld")
        assert "\n" not in result
        assert "hello world" in result
        # Backticks removed
        assert "`" not in registry._sanitize_task_context("`code`")
        # Hash removed
        assert "#" not in registry._sanitize_task_context("## header")
        # Comment markers removed
        assert "/*" not in registry._sanitize_task_context("/* comment */")
        assert "*/" not in registry._sanitize_task_context("/* comment */")
