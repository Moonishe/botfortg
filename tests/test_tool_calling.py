"""Tests for LLM tool calling infrastructure."""

from __future__ import annotations

import pytest

from src.llm.base import ChatMessage
from src.llm.base_provider import BaseLLMProvider
from src.llm.tool_calling.models import (
    ChatResponse,
    ToolCall,
    ToolCallResult,
    ToolDefinition,
)
from src.llm.tool_calling.registry_adapter import (
    ToolRegistryAdapter,
    _params_to_json_schema,
)
from src.llm.tool_calling.loop import ToolCallingLoop
from src.core.actions.tool_registry import ToolRegistry, ToolSpec, tool_registry


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def empty_registry() -> ToolRegistry:
    """A fresh ToolRegistry with no tools."""
    reg = ToolRegistry()
    return reg


@pytest.fixture
def registry_with_sample() -> ToolRegistry:
    """A registry with one sample tool that has input_schema."""
    reg = ToolRegistry()
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {"type": "integer", "description": "Max results", "default": 5},
        },
        "required": ["query"],
    }

    async def sample_search(**kwargs):
        return {"ok": True, "results": [], "query": kwargs.get("query", "")}

    spec = ToolSpec(
        name="sample_search",
        description="Search for things",
        category="search",
        handler=sample_search,
        params={"query": "str", "limit": "int|None"},
        input_schema=input_schema,
    )
    reg.register(spec)
    return reg


# ── _params_to_json_schema ───────────────────────────────────────────


class TestParamsToJsonSchema:
    def test_basic_params(self):
        schema = _params_to_json_schema({"query": "str", "limit": "int|None"})
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert "limit" in schema["properties"]
        assert schema["properties"]["query"] == {"type": "string"}
        # nullable: two types joined → array
        assert schema["properties"]["limit"] == {"type": ["number", "null"]}
        assert "query" in schema["required"]
        assert "limit" not in schema["required"]  # nullable → not required

    def test_skip_internal_params(self):
        schema = _params_to_json_schema({"_confirmed": "bool", "query": "str"})
        assert "_confirmed" not in schema["properties"]
        assert "_admin_confirmed" not in schema["properties"]
        assert "query" in schema["required"]

    def test_empty_params(self):
        schema = _params_to_json_schema({})
        assert schema == {"type": "object", "properties": {}, "required": []}


# ── ToolRegistryAdapter ──────────────────────────────────────────────


class TestToolRegistryAdapter:
    def test_get_tool_definitions_with_input_schema(self, registry_with_sample):
        adapter = ToolRegistryAdapter(registry=registry_with_sample)
        defs = adapter.get_tool_definitions(available_only=False)
        assert len(defs) == 1
        assert defs[0].name == "sample_search"
        assert defs[0].description == "Search for things"
        assert (
            defs[0].parameters == registry_with_sample.get("sample_search").input_schema
        )

    def test_get_tool_definitions_empty(self, empty_registry):
        adapter = ToolRegistryAdapter(registry=empty_registry)
        defs = adapter.get_tool_definitions(available_only=False)
        assert defs == []

    def test_get_tool_definitions_by_names(self, registry_with_sample):
        adapter = ToolRegistryAdapter(registry=registry_with_sample)
        defs = adapter.get_tool_definitions(
            available_only=False, names=["sample_search"]
        )
        assert len(defs) == 1
        assert defs[0].name == "sample_search"

    def test_get_tool_definitions_unknown_name(self, registry_with_sample):
        adapter = ToolRegistryAdapter(registry=registry_with_sample)
        defs = adapter.get_tool_definitions(available_only=False, names=["nonexistent"])
        assert defs == []

    def test_get_tool_definitions_by_categories(self, registry_with_sample):
        adapter = ToolRegistryAdapter(registry=registry_with_sample)
        defs = adapter.get_tool_definitions(available_only=False, categories=["search"])
        assert len(defs) == 1

        defs_other = adapter.get_tool_definitions(
            available_only=False, categories=["memory"]
        )
        assert defs_other == []

    def test_get_tool_definitions_params_no_schema(self, empty_registry):
        async def echo(**kwargs):
            return {"ok": True}

        spec = ToolSpec(
            name="echo",
            description="Echo back",
            category="utility",
            handler=echo,
            params={"message": "str"},
            input_schema=None,
        )
        empty_registry.register(spec)

        adapter = ToolRegistryAdapter(registry=empty_registry)
        defs = adapter.get_tool_definitions(available_only=False)
        assert len(defs) == 1
        # Should generate schema from params
        assert defs[0].parameters["type"] == "object"
        assert "message" in defs[0].parameters["properties"]

    @pytest.mark.asyncio
    async def test_execute_delegates_to_registry(self, registry_with_sample):
        adapter = ToolRegistryAdapter(registry=registry_with_sample)
        tc = ToolCall(id="call_1", name="sample_search", arguments={"query": "test"})
        result = await adapter.execute(tc)
        assert isinstance(result, ToolCallResult)
        assert result.tool_call_id == "call_1"
        assert result.name == "sample_search"
        assert result.error is None
        assert result.result is not None
        assert result.result.get("ok") is True

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, empty_registry):
        adapter = ToolRegistryAdapter(registry=empty_registry)
        tc = ToolCall(id="call_x", name="nonexistent", arguments={})
        result = await adapter.execute(tc)
        assert result.error is None  # ToolRegistry returns dict with error key
        assert result.result is not None
        assert "error" in result.result


# ── ToolCallResult ───────────────────────────────────────────────────


class TestToolCallResult:
    def test_format_for_llm_success(self):
        r = ToolCallResult(
            tool_call_id="id1",
            name="test",
            result={"ok": True, "data": [1, 2, 3]},
        )
        formatted = r.format_for_llm()
        assert '"ok"' in formatted
        assert '"data"' in formatted

    def test_format_for_llm_error(self):
        r = ToolCallResult(
            tool_call_id="id1",
            name="test",
            error="Something went wrong",
        )
        formatted = r.format_for_llm()
        assert "error" in formatted
        assert "Something went wrong" in formatted

    def test_format_for_llm_no_result(self):
        r = ToolCallResult(tool_call_id="id1", name="test")
        formatted = r.format_for_llm()
        assert '"ok"' in formatted


# ── ToolCallingLoop ──────────────────────────────────────────────────


class TestToolCallingLoop:
    @pytest.mark.asyncio
    async def test_loop_single_tool_call_then_done(self, registry_with_sample):
        """First call returns tool_calls, second returns text. Verify final text."""
        adapter = ToolRegistryAdapter(registry=registry_with_sample)

        call_count = [0]

        class MockProvider:
            async def chat_with_tools(
                self, messages, tools=None, *, task_type="default"
            ):
                call_count[0] += 1
                if call_count[0] == 1:
                    return ChatResponse(
                        text="",
                        tool_calls=[
                            ToolCall(
                                id="call_1",
                                name="sample_search",
                                arguments={"query": "hello"},
                            )
                        ],
                    )
                else:
                    return ChatResponse(text="done", tool_calls=None)

            async def chat(self, messages, *, heavy=False, task_type="default"):
                return "done"

        loop = ToolCallingLoop(adapter=adapter, max_iterations=5)
        messages = [ChatMessage(role="user", content="search for hello")]
        response = await loop.iterate(MockProvider(), messages)

        assert response.text == "done"
        assert response.tool_calls is None
        assert call_count[0] == 2
        # Verify assistant + tool messages were appended
        assert len(messages) == 3  # user + assistant + tool

    @pytest.mark.asyncio
    async def test_loop_no_tool_calls_first_try(self, registry_with_sample):
        """LLM responds with text immediately — no loop."""
        adapter = ToolRegistryAdapter(registry=registry_with_sample)

        class MockProvider:
            async def chat_with_tools(
                self, messages, tools=None, *, task_type="default"
            ):
                return ChatResponse(text="no tools needed", tool_calls=None)

            async def chat(self, messages, *, heavy=False, task_type="default"):
                return "no tools needed"

        loop = ToolCallingLoop(adapter=adapter, max_iterations=5)
        messages = [ChatMessage(role="user", content="hi")]
        response = await loop.iterate(MockProvider(), messages)

        assert response.text == "no tools needed"
        assert len(messages) == 1  # unchanged

    @pytest.mark.asyncio
    async def test_loop_not_implemented_fallback(self, registry_with_sample):
        """Provider raises NotImplementedError — fall back to chat()."""
        adapter = ToolRegistryAdapter(registry=registry_with_sample)

        class MockProvider:
            async def chat_with_tools(
                self, messages, tools=None, *, task_type="default"
            ):
                raise NotImplementedError("no tools")

            async def chat(self, messages, *, heavy=False, task_type="default"):
                return "fallback chat"

        loop = ToolCallingLoop(adapter=adapter, max_iterations=5)
        messages = [ChatMessage(role="user", content="hi")]
        response = await loop.iterate(MockProvider(), messages)

        assert response.text == "fallback chat"
        assert response.tool_calls is None

    @pytest.mark.asyncio
    async def test_loop_multiple_tool_calls(self, registry_with_sample):
        """LLM calls tool twice before finishing."""
        adapter = ToolRegistryAdapter(registry=registry_with_sample)

        call_count = [0]

        class MockProvider:
            async def chat_with_tools(
                self, messages, tools=None, *, task_type="default"
            ):
                call_count[0] += 1
                if call_count[0] == 1:
                    return ChatResponse(
                        text="",
                        tool_calls=[
                            ToolCall(
                                id="call_a",
                                name="sample_search",
                                arguments={"query": "first"},
                            )
                        ],
                    )
                elif call_count[0] == 2:
                    return ChatResponse(
                        text="",
                        tool_calls=[
                            ToolCall(
                                id="call_b",
                                name="sample_search",
                                arguments={"query": "second"},
                            )
                        ],
                    )
                else:
                    return ChatResponse(text="all done", tool_calls=None)

            async def chat(self, messages, *, heavy=False, task_type="default"):
                return "all done"

        loop = ToolCallingLoop(adapter=adapter, max_iterations=5)
        messages = [ChatMessage(role="user", content="search twice")]
        response = await loop.iterate(MockProvider(), messages)

        assert response.text == "all done"
        assert call_count[0] == 3
        # user + assistant + tool + assistant + tool = 5 messages
        assert len(messages) == 5


# ── _fmt_messages tool serialization ────────────────────────────────
# We test BaseLLMProvider._fmt_messages indirectly via a concrete subclass.


class TestFmtMessages:
    @pytest.mark.asyncio
    async def test_fmt_messages_serializes_tool_fields(self):
        """Verify _fmt_messages() includes tool_calls, tool_call_id, name."""

        # Create a minimal provider that inherits BaseLLMProvider
        class MinimalProvider(BaseLLMProvider):
            name = "minimal"
            _LIGHT_MODEL = "test"
            _HEAVY_MODEL = "test"

            async def chat(self, messages, *, heavy=False, task_type="default"):
                return "ok"

            async def chat_stream(self, messages, *, heavy=False, task_type="default"):
                raise NotImplementedError

            async def validate_key(self):
                return True

            async def close(self):
                pass

        provider = MinimalProvider(api_key="test")

        messages = [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(
                role="assistant",
                content="Let me check",
                tool_calls=[
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "search",
                            "arguments": '{"query":"test"}',
                        },
                    }
                ],
            ),
            ChatMessage(
                role="tool",
                content='{"ok":true}',
                tool_call_id="call_123",
            ),
        ]

        fmt = provider._fmt_messages(messages)

        assert len(fmt) == 4
        # system
        assert fmt[0] == {"role": "system", "content": "You are helpful."}
        # user
        assert fmt[1] == {"role": "user", "content": "Hello"}
        # assistant with tool_calls
        assert fmt[2]["role"] == "assistant"
        assert fmt[2]["content"] == "Let me check"
        assert "tool_calls" in fmt[2]
        assert fmt[2]["tool_calls"][0]["id"] == "call_123"
        # tool message
        assert fmt[3]["role"] == "tool"
        assert fmt[3]["content"] == '{"ok":true}'
        assert fmt[3]["tool_call_id"] == "call_123"

    def test_tools_to_openai(self):
        """Verify _tools_to_openai() produces correct OpenAI format."""
        from src.llm.tool_calling.models import ToolDefinition

        tools = [
            ToolDefinition(
                name="search",
                description="Search for things",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        ]

        openai_tools = BaseLLMProvider._tools_to_openai(tools)

        assert len(openai_tools) == 1
        assert openai_tools[0]["type"] == "function"
        assert openai_tools[0]["function"]["name"] == "search"
        assert openai_tools[0]["function"]["description"] == "Search for things"
        assert "parameters" in openai_tools[0]["function"]


# ── Edge case tests ──────────────────────────────────────────────────


class TestEdgeCases:
    """Tests for edge cases: malformed JSON, max iterations, empty tools."""

    @pytest.mark.asyncio
    async def test_loop_max_iterations_reached(self, registry_with_sample):
        """When max_iterations is reached, loop returns fallback text."""
        adapter = ToolRegistryAdapter(registry=registry_with_sample)

        class MockProvider:
            async def chat_with_tools(
                self, messages, tools=None, *, task_type="default"
            ):
                # Always returns tool calls — forces max iterations
                return ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="call_repeat",
                            name="sample_search",
                            arguments={"query": "forever"},
                        )
                    ],
                )

            async def chat(self, messages, *, heavy=False, task_type="default"):
                return "fallback"

        loop = ToolCallingLoop(adapter=adapter, max_iterations=2)
        messages = [ChatMessage(role="user", content="infinite loop")]
        response = await loop.iterate(MockProvider(), messages)

        assert "Maximum tool-calling iterations reached" in response.text
        assert response.tool_calls is None

    @pytest.mark.asyncio
    async def test_loop_empty_tool_calls_list(self, registry_with_sample):
        """Empty tool_calls list (not None) should stop the loop."""
        adapter = ToolRegistryAdapter(registry=registry_with_sample)

        class MockProvider:
            async def chat_with_tools(
                self, messages, tools=None, *, task_type="default"
            ):
                return ChatResponse(text="done", tool_calls=[])

            async def chat(self, messages, *, heavy=False, task_type="default"):
                return "done"

        loop = ToolCallingLoop(adapter=adapter, max_iterations=5)
        messages = [ChatMessage(role="user", content="hi")]
        response = await loop.iterate(MockProvider(), messages)

        assert response.text == "done"
        assert response.tool_calls == []

    def test_safe_parse_tool_args_valid(self):
        """safe_parse_tool_args parses valid JSON correctly."""
        from src.llm.tool_calling.models import safe_parse_tool_args

        result = safe_parse_tool_args('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_safe_parse_tool_args_malformed(self):
        """Malformed JSON returns empty dict."""
        from src.llm.tool_calling.models import safe_parse_tool_args

        result = safe_parse_tool_args("not json")
        assert result == {}

    def test_safe_parse_tool_args_null(self):
        """null JSON returns empty dict."""
        from src.llm.tool_calling.models import safe_parse_tool_args

        result = safe_parse_tool_args("null")
        assert result == {}

    def test_safe_parse_tool_args_empty_string(self):
        """Empty string returns empty dict."""
        from src.llm.tool_calling.models import safe_parse_tool_args

        result = safe_parse_tool_args("")
        assert result == {}

    def test_safe_parse_tool_args_non_dict(self):
        """Non-dict JSON values return empty dict."""
        from src.llm.tool_calling.models import safe_parse_tool_args

        result = safe_parse_tool_args("[1, 2, 3]")
        assert result == {}

    def test_dict_to_json_with_non_serializable(self):
        """_dict_to_json handles non-serializable values gracefully."""
        from src.llm.tool_calling.loop import _dict_to_json

        # bytes is not JSON-serializable
        result = _dict_to_json({"data": b"binary"})
        # Should not crash — uses default=repr fallback
        assert isinstance(result, str)
        assert "binary" in result or "error" in result.lower()

    def test_dict_to_json_normal(self):
        """_dict_to_json serializes normal dicts."""
        from src.llm.tool_calling.loop import _dict_to_json

        result = _dict_to_json({"key": "value"})
        import json

        assert result == json.dumps({"key": "value"}, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_loop_with_empty_registry_no_tools(self, empty_registry):
        """Loop works when tool registry returns no tool definitions."""
        adapter = ToolRegistryAdapter(registry=empty_registry)

        class MockProvider:
            async def chat_with_tools(
                self, messages, tools=None, *, task_type="default"
            ):
                # tools should be None (no definitions available)
                return ChatResponse(text="done", tool_calls=None)

            async def chat(self, messages, *, heavy=False, task_type="default"):
                return "done"

        loop = ToolCallingLoop(adapter=adapter, max_iterations=5)
        messages = [ChatMessage(role="user", content="hi")]
        response = await loop.iterate(MockProvider(), messages)

        assert response.text == "done"
        assert response.tool_calls is None
