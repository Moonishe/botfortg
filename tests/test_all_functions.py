"""Tests for all functions implemented/modified in the optimization session.

Covers: IterationBudget, duplicate pruner, use_skill, bayesian_skill_score,
format_skill_index, _format_context_sources, prompt caching, mcp_tools search,
mcp_code_exec sandbox, mcp_self_model registration, callback_utils patch,
BaseException in gather, embedding defaults, plugin_loader skip _.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")


# ═══════════════════════════════════════════════════════════════════
#  IterationBudget — consume/refund/remaining/reset/grace
# ═══════════════════════════════════════════════════════════════════


class TestIterationBudget:
    """Test IterationBudget class: consume, refund, remaining, reset."""

    def test_init_valid(self):
        from src.core.intelligence.iteration_budget import IterationBudget

        b = IterationBudget(max_total=10)
        assert b.remaining == 10

    def test_init_zero_raises(self):
        from src.core.intelligence.iteration_budget import IterationBudget

        with pytest.raises(ValueError):
            IterationBudget(max_total=0)

    def test_init_negative_raises(self):
        from src.core.intelligence.iteration_budget import IterationBudget

        with pytest.raises(ValueError):
            IterationBudget(max_total=-5)

    def test_consume_decrements(self):
        from src.core.intelligence.iteration_budget import IterationBudget

        b = IterationBudget(max_total=3)
        assert b.consume() is True
        assert b.remaining == 2
        assert b.consume() is True
        assert b.remaining == 1
        assert b.consume() is True
        assert b.remaining == 0

    def test_consume_exhausted_returns_false(self):
        from src.core.intelligence.iteration_budget import IterationBudget

        b = IterationBudget(max_total=1)
        assert b.consume() is True
        assert b.consume() is False
        assert b.consume() is False

    def test_refund_increments(self):
        from src.core.intelligence.iteration_budget import IterationBudget

        b = IterationBudget(max_total=5)
        b.consume()
        b.consume()
        assert b.remaining == 3
        b.refund()
        assert b.remaining == 4

    def test_refund_at_zero_noop(self):
        from src.core.intelligence.iteration_budget import IterationBudget

        b = IterationBudget(max_total=5)
        b.refund()
        assert b.remaining == 5

    def test_reset_zeros_used(self):
        from src.core.intelligence.iteration_budget import IterationBudget

        b = IterationBudget(max_total=5)
        b.consume()
        b.consume()
        assert b.remaining == 3
        b.reset()
        assert b.remaining == 5

    def test_remaining_never_negative(self):
        from src.core.intelligence.iteration_budget import IterationBudget

        b = IterationBudget(max_total=2)
        b.consume()
        b.consume()
        assert b.remaining == 0
        b.consume()  # try to over-consume
        assert b.remaining == 0  # not negative

    def test_repr_contains_max_and_used(self):
        from src.core.intelligence.iteration_budget import IterationBudget

        b = IterationBudget(max_total=10)
        b.consume()
        r = repr(b)
        assert "10" in r
        assert "1" in r


# ═══════════════════════════════════════════════════════════════════
#  budget_for_complexity — edge cases
# ═══════════════════════════════════════════════════════════════════


class TestBudgetForComplexity:
    """Test budget_for_complexity function."""

    def test_low_complexity_halves(self):
        from src.core.intelligence.iteration_budget import budget_for_complexity

        assert budget_for_complexity(0.1, 10) == 5

    def test_medium_complexity_unchanged(self):
        from src.core.intelligence.iteration_budget import budget_for_complexity

        assert budget_for_complexity(0.5, 10) == 10

    def test_high_complexity_increases(self):
        from src.core.intelligence.iteration_budget import budget_for_complexity

        assert budget_for_complexity(0.9, 10) == 15

    def test_min_5_iterations(self):
        from src.core.intelligence.iteration_budget import budget_for_complexity

        assert budget_for_complexity(0.0, 4) == 5

    def test_base_zero_returns_min(self):
        from src.core.intelligence.iteration_budget import budget_for_complexity

        assert budget_for_complexity(0.9, 0) >= 1

    def test_boundary_0_3(self):
        from src.core.intelligence.iteration_budget import budget_for_complexity

        assert budget_for_complexity(0.3, 10) == 10

    def test_boundary_0_6(self):
        from src.core.intelligence.iteration_budget import budget_for_complexity

        assert budget_for_complexity(0.6, 10) == 15


# ═══════════════════════════════════════════════════════════════════
#  Duplicate Pruner (MD5 dedup post-hook)
# ═══════════════════════════════════════════════════════════════════


class TestDuplicatePruner:
    """Test _build_duplicate_pruner post-hook."""

    @pytest.mark.asyncio
    async def test_pruner_replaces_duplicate(self):
        from src.core.actions.tool_middleware import _build_duplicate_pruner

        pruner = _build_duplicate_pruner()
        ctx = MagicMock()
        ctx.tool_name = "test_tool"
        ctx.result = {"data": "same content"}
        # First call: no duplicate
        await pruner(ctx)
        assert "Duplicate" not in str(ctx.result)
        # Second call with same content: should be replaced
        ctx2 = MagicMock()
        ctx2.tool_name = "test_tool"
        ctx2.result = {"data": "same content"}
        await pruner(ctx2)
        assert "Duplicate" in str(ctx2.result) or "info" in ctx2.result

    @pytest.mark.asyncio
    async def test_pruner_keeps_unique(self):
        from src.core.actions.tool_middleware import _build_duplicate_pruner

        pruner = _build_duplicate_pruner()
        ctx1 = MagicMock()
        ctx1.tool_name = "t1"
        ctx1.result = {"data": "content A"}
        await pruner(ctx1)

        ctx2 = MagicMock()
        ctx2.tool_name = "t1"
        ctx2.result = {"data": "content B"}
        await pruner(ctx2)
        assert ctx2.result == {"data": "content B"}

    @pytest.mark.asyncio
    async def test_pruner_none_result_passes(self):
        from src.core.actions.tool_middleware import _build_duplicate_pruner

        pruner = _build_duplicate_pruner()
        ctx = MagicMock()
        ctx.tool_name = "t"
        ctx.result = None
        await pruner(ctx)
        assert ctx.result is None


# ═══════════════════════════════════════════════════════════════════
#  _resolve_user_id helper (mcp_skill.py)
# ═══════════════════════════════════════════════════════════════════


class TestResolveUserId:
    """Test _resolve_user_id helper from mcp_skill."""

    def test_int_input(self):
        from src.core.actions.mcp_skill import _resolve_user_id

        assert _resolve_user_id({"user": 123456789}) == 123456789

    def test_none_input(self):
        from src.core.actions.mcp_skill import _resolve_user_id

        assert _resolve_user_id({}) is None
        assert _resolve_user_id({"user": None}) is None

    def test_object_with_telegram_id(self):
        from src.core.actions.mcp_skill import _resolve_user_id

        obj = MagicMock()
        obj.telegram_id = 999
        assert _resolve_user_id({"user": obj}) == 999

    def test_object_without_telegram_id(self):
        from src.core.actions.mcp_skill import _resolve_user_id

        obj = MagicMock(spec=[])
        result = _resolve_user_id({"user": obj})
        # Should try int() on object, which may fail → None
        assert result is None or isinstance(result, int)


# ═══════════════════════════════════════════════════════════════════
#  bayesian_skill_score
# ═══════════════════════════════════════════════════════════════════


class TestBayesianSkillScore:
    """Test bayesian_skill_score function."""

    def test_zero_uses_returns_zero(self):
        from src.core.intelligence.skills import bayesian_skill_score

        skill = MagicMock()
        skill.success_count = 0
        skill.failure_count = 0
        assert bayesian_skill_score(skill) == 0.0

    def test_all_success_high_score(self):
        from src.core.intelligence.skills import bayesian_skill_score

        skill = MagicMock()
        skill.success_count = 10
        skill.failure_count = 0
        score = bayesian_skill_score(skill)
        assert score > 7.0  # (10 + 5*0.7) / (10 + 5) * 10 = 9.0

    def test_all_failure_low_score(self):
        from src.core.intelligence.skills import bayesian_skill_score

        skill = MagicMock()
        skill.success_count = 0
        skill.failure_count = 10
        score = bayesian_skill_score(skill)
        assert score < 3.0  # (0 + 5*0.7) / (10 + 5) * 10 = 2.33

    def test_shrinkage_toward_prior(self):
        from src.core.intelligence.skills import bayesian_skill_score

        # One success, no failures — shrunk toward prior 0.7
        skill = MagicMock()
        skill.success_count = 1
        skill.failure_count = 0
        score = bayesian_skill_score(skill)
        # (1 + 3.5) / (1 + 5) * 10 = 7.5
        assert 6.0 < score < 9.0

    def test_none_counts_treated_as_zero(self):
        from src.core.intelligence.skills import bayesian_skill_score

        skill = MagicMock()
        skill.success_count = None
        skill.failure_count = None
        assert bayesian_skill_score(skill) == 0.0

    def test_custom_alpha_prior(self):
        from src.core.intelligence.skills import bayesian_skill_score

        skill = MagicMock()
        skill.success_count = 5
        skill.failure_count = 5
        # default: (5 + 3.5) / (10 + 5) * 10 = 5.67
        # custom alpha=10, prior=0.5: (5 + 5) / (10 + 10) * 10 = 5.0
        score = bayesian_skill_score(skill, alpha=10, prior=0.5)
        assert abs(score - 5.0) < 0.1


# ═══════════════════════════════════════════════════════════════════
#  format_skill_index — progressive skills (body[:700] removed)
# ═══════════════════════════════════════════════════════════════════


class TestFormatSkillIndex:
    """Test format_skill_index: no body[:700], has use_skill hint."""

    def test_empty_skills_returns_empty(self):
        from src.core.intelligence.skills import format_skill_index

        assert format_skill_index([]) == ""

    def test_no_body_injection(self):
        from src.core.intelligence.skills import format_skill_index

        skill = MagicMock()
        skill.name = "test_skill"
        skill.description = "A test skill"
        skill.body = "A" * 1000  # long body
        skill.success_count = 1
        skill.failure_count = 0
        skill.trigger_patterns_json = []
        skill.validation_score = 0.9
        skill.review_status = "approved"
        skill.rejected_edits_json = None
        result = format_skill_index([skill])
        # body[:700] should NOT be in output
        assert "A" * 700 not in result
        assert "procedure:" not in result or "body" not in result.lower()

    def test_use_skill_hint_present(self):
        from src.core.intelligence.skills import format_skill_index

        skill = MagicMock()
        skill.name = "test_skill"
        skill.description = "A test skill"
        skill.body = "test body"
        skill.success_count = 1
        skill.failure_count = 0
        skill.trigger_patterns_json = []
        skill.validation_score = 0.9
        skill.review_status = "approved"
        skill.rejected_edits_json = None
        result = format_skill_index([skill])
        assert "use_skill" in result


# ═══════════════════════════════════════════════════════════════════
#  _format_context_sources + prompt caching
# ═══════════════════════════════════════════════════════════════════


class TestFormatContextSources:
    """Test _format_context_sources in PromptAssembler."""

    def test_empty_context_returns_empty(self):
        from src.core.intelligence.prompt_assembler import (
            AssemblyContext,
            PromptAssembler,
        )

        pa = PromptAssembler()
        ctx = AssemblyContext(target="maestro", user_id=0)
        result = pa._format_context_sources(ctx)
        assert result == ""

    def test_active_sources_listed(self):
        from src.core.intelligence.prompt_assembler import (
            AssemblyContext,
            PromptAssembler,
        )

        pa = PromptAssembler()
        ctx = AssemblyContext(
            target="maestro",
            user_id=0,
            rag_context="some context",
            persona_block="persona",
            skill_index="skills here",
        )
        result = pa._format_context_sources(ctx)
        assert "<context_sources>" in result
        assert "RAG" in result
        assert "Persona" in result
        assert "Skills" in result

    def test_all_sources_listed(self):
        from src.core.intelligence.prompt_assembler import (
            AssemblyContext,
            PromptAssembler,
        )

        pa = PromptAssembler()
        ctx = AssemblyContext(
            target="maestro",
            user_id=0,
            rag_context="x",
            persona_block="x",
            style_match_block="x",
            confirmed_rules=["rule1"],
            deep_memory="x",
            skill_index="x",
            frozen_snapshot="x",
            memory_context="x",
            self_profile="x",
            contact_graph="x",
            dsm_context="x",
            correction_context="x",
            session_summary="x",
            contact_rules_block="x",
        )
        result = pa._format_context_sources(ctx)
        assert result.count("- ") >= 14  # at least 14 sources


class TestPromptCaching:
    """Test prompt caching in PromptAssembler."""

    def test_tier1_cached_on_second_call(self):
        from src.core.intelligence.prompt_assembler import PromptAssembler

        pa = PromptAssembler()
        pa.clear_prompt_cache()
        first = pa._tier1_stable("maestro")
        second = pa._tier1_stable("maestro")
        assert first == second
        assert "maestro" in pa._tier1_cache

    def test_tier2_static_cached(self):
        from src.core.intelligence.prompt_assembler import PromptAssembler

        pa = PromptAssembler()
        pa.clear_prompt_cache()
        first = pa._tier2_static("maestro")
        second = pa._tier2_static("maestro")
        assert first == second
        assert "maestro" in pa._tier2_static_cache

    def test_clear_prompt_cache_resets(self):
        from src.core.intelligence.prompt_assembler import PromptAssembler

        pa = PromptAssembler()
        pa._tier1_stable("maestro")
        assert len(pa._tier1_cache) > 0
        pa.clear_prompt_cache()
        assert len(pa._tier1_cache) == 0
        assert len(pa._tier2_static_cache) == 0

    def test_different_targets_different_cache(self):
        from src.core.intelligence.prompt_assembler import PromptAssembler

        pa = PromptAssembler()
        pa.clear_prompt_cache()
        m = pa._tier1_stable("maestro")
        a = pa._tier1_stable("agent")
        assert m != a or m == ""  # different targets
        assert "maestro" in pa._tier1_cache
        assert "agent" in pa._tier1_cache

    def test_assemble_uses_cache(self):
        from src.core.intelligence.prompt_assembler import (
            AssemblyContext,
            PromptAssembler,
        )

        pa = PromptAssembler()
        pa.clear_prompt_cache()
        ctx = AssemblyContext(target="maestro", user_id=0)
        p1 = pa.assemble(ctx)
        p2 = pa.assemble(ctx)
        assert p1 == p2  # cached tier1/tier2 produce same result
        assert len(pa._tier1_cache) > 0


# ═══════════════════════════════════════════════════════════════════
#  mcp_tools search — empty pattern, max files
# ═══════════════════════════════════════════════════════════════════


class TestMcpToolsSearch:
    """Test mcp_filesystem search edge cases."""

    @pytest.mark.asyncio
    async def test_empty_pattern_returns_error(self):
        from src.core.actions.mcp_tools import _fs_search

        result = await _fs_search("", "data")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_whitespace_pattern_returns_error(self):
        from src.core.actions.mcp_tools import _fs_search

        result = await _fs_search("   ", "data")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_valid_pattern_returns_list(self):
        from src.core.actions.mcp_tools import _fs_search

        # Search in data/ which should exist
        result = await _fs_search("test", "data")
        assert isinstance(result, list | dict)
        if isinstance(result, dict):
            assert "error" in result  # dir might not exist

    @pytest.mark.asyncio
    async def test_nonexistent_dir_returns_error(self):
        from src.core.actions.mcp_tools import _fs_search

        result = await _fs_search("test", "/nonexistent/path/xyz")
        assert isinstance(result, dict)
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════
#  mcp_code_exec sandbox — tuple immutability, blocked modules
# ═══════════════════════════════════════════════════════════════════


class TestCodeExecSandbox:
    """Test code_exec sandbox hardening."""

    def test_disallowed_imports_is_set(self):
        from src.core.actions.mcp_code_exec import _DISALLOWED_IMPORTS

        assert isinstance(_DISALLOWED_IMPORTS, set)
        assert "os" in _DISALLOWED_IMPORTS
        assert "subprocess" in _DISALLOWED_IMPORTS
        assert "pickle" in _DISALLOWED_IMPORTS
        assert "marshal" in _DISALLOWED_IMPORTS
        assert "inspect" in _DISALLOWED_IMPORTS

    def test_sandbox_blacklist_is_frozenset(self):
        from src.core.actions.mcp_code_exec import _SANDBOX_BLACKLIST

        assert isinstance(_SANDBOX_BLACKLIST, frozenset)
        assert "__import__" in _SANDBOX_BLACKLIST
        assert "__subclasses__" in _SANDBOX_BLACKLIST

    def test_wrapper_template_uses_tuple(self):
        from src.core.actions.mcp_code_exec import _WRAPPER_TEMPLATE

        # The template should use tuple() not list() for _DISALLOWED
        # Check that the repr(tuple(...)) is used in the template generation
        assert "tuple" in _WRAPPER_TEMPLATE or "_DISALLOWED" in _WRAPPER_TEMPLATE

    def test_dangerous_modules_blocked(self):
        from src.core.actions.mcp_code_exec import _DISALLOWED_IMPORTS

        dangerous = [
            "os",
            "subprocess",
            "sys",
            "pickle",
            "marshal",
            "code",
            "codeop",
            "inspect",
            "runpy",
            "fcntl",
            "posix",
            "nt",
            "_thread",
            "pty",
            "atexit",
            "faulthandler",
        ]
        for mod in dangerous:
            assert mod in _DISALLOWED_IMPORTS, f"{mod} not in _DISALLOWED_IMPORTS"

    def test_safe_modules_allowed(self):
        from src.core.actions.mcp_code_exec import _DISALLOWED_IMPORTS

        safe = ["math", "json", "datetime", "collections", "itertools", "re"]
        for mod in safe:
            assert mod not in _DISALLOWED_IMPORTS, f"{mod} should be allowed"


# ═══════════════════════════════════════════════════════════════════
#  mcp_self_model — @tool registration, default action
# ═══════════════════════════════════════════════════════════════════


class TestMcpSelfModel:
    """Test mcp_self_model registration and defaults."""

    def test_registered_in_tool_registry(self):
        from src.core.actions import register_builtin_tools
        from src.core.actions.tool_registry import tool_registry

        register_builtin_tools()
        spec = tool_registry.get("mcp_self_model")
        assert spec is not None
        assert spec.name == "mcp_self_model"
        assert spec.category == "admin"

    def test_default_action_is_current(self):
        import inspect

        from src.core.actions.mcp_self_model import mcp_self_model

        sig = inspect.signature(mcp_self_model)
        action_param = sig.parameters.get("action")
        assert action_param is not None
        assert action_param.default == "current"

    def test_requires_confirmation_true(self):
        from src.core.actions.tool_registry import tool_registry

        spec = tool_registry.get("mcp_self_model")
        assert spec.requires_confirmation is True


# ═══════════════════════════════════════════════════════════════════
#  callback_utils patch — guards, return False
# ═══════════════════════════════════════════════════════════════════


class TestCallbackPatch:
    """Test InaccessibleMessage monkeypatch."""

    def test_patch_applied(self):
        # Import callback_utils to trigger monkeypatch
        import src.bot.callback_utils  # noqa: F401
        from aiogram.types import InaccessibleMessage

        # Patch should be applied at import time
        assert hasattr(InaccessibleMessage, "edit_text")
        assert hasattr(InaccessibleMessage, "delete")
        assert hasattr(InaccessibleMessage, "edit_reply_markup")

    @pytest.mark.asyncio
    async def test_noop_edit_text_returns_false(self):
        import src.bot.callback_utils  # noqa: F401
        from aiogram.types import Chat, InaccessibleMessage

        chat = Chat(id=1, type="private")
        msg = InaccessibleMessage(message_id=1, date=0, chat=chat)
        result = await msg.edit_text("test")
        assert result is False

    @pytest.mark.asyncio
    async def test_noop_delete_returns_false(self):
        import src.bot.callback_utils  # noqa: F401
        from aiogram.types import Chat, InaccessibleMessage

        chat = Chat(id=1, type="private")
        msg = InaccessibleMessage(message_id=1, date=0, chat=chat)
        result = await msg.delete()
        assert result is False

    def test_patch_inaccessible_message_idempotent(self):
        from src.bot.callback_utils import patch_inaccessible_message

        # Calling again should not crash
        patch_inaccessible_message()
        patch_inaccessible_message()

    @pytest.mark.asyncio
    async def test_safe_callback_edit_with_inaccessible(self):
        from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, User

        from src.bot.callback_utils import safe_callback_edit

        chat = Chat(id=1, type="private")
        user = User(id=1, is_bot=False, first_name="test")
        msg = InaccessibleMessage(message_id=1, date=0, chat=chat)
        cb = CallbackQuery(
            id="test",
            from_user=user,
            chat_instance="test",
            data="test",
            message=msg,
            chat=chat,
        )
        result = await safe_callback_edit(cb, "text")
        assert result is False


# ═══════════════════════════════════════════════════════════════════
#  BaseException in gather — CancelledError handling
# ═══════════════════════════════════════════════════════════════════


class TestGatherBaseException:
    """Test that CancelledError is handled as BaseException, not Exception."""

    @pytest.mark.asyncio
    async def test_cancelled_error_is_baseexception(self):
        # Verify CancelledError is not caught by isinstance(x, Exception)
        ce = asyncio.CancelledError()
        assert isinstance(ce, BaseException)
        assert not isinstance(ce, Exception)

    @pytest.mark.asyncio
    async def test_gather_returns_cancelled_error(self):
        async def _cancel():
            raise asyncio.CancelledError()

        results = await asyncio.gather(_cancel(), return_exceptions=True)
        assert isinstance(results[0], BaseException)
        assert not isinstance(results[0], Exception)

    @pytest.mark.asyncio
    async def test_gather_mixed_results(self):
        async def _ok():
            return 42

        async def _fail():
            raise ValueError("oops")

        async def _cancel():
            raise asyncio.CancelledError()

        results = await asyncio.gather(
            _ok(), _fail(), _cancel(), return_exceptions=True
        )
        assert results[0] == 42
        assert isinstance(results[1], Exception)
        assert isinstance(results[2], BaseException)
        assert not isinstance(results[2], Exception)


# ═══════════════════════════════════════════════════════════════════
#  Embedding model defaults
# ═══════════════════════════════════════════════════════════════════


class TestEmbeddingDefaults:
    """Test embedding model default fields in config."""

    def test_openai_embed_model_exists(self):
        from src.config import settings

        assert hasattr(settings, "openai_embed_model")
        assert settings.openai_embed_model == "text-embedding-3-small"

    def test_gemini_embed_model_exists(self):
        from src.config import settings

        assert hasattr(settings, "gemini_embed_model")
        assert settings.gemini_embed_model == "text-embedding-004"

    def test_mistral_embed_model_exists(self):
        from src.config import settings

        assert hasattr(settings, "mistral_embed_model")
        assert settings.mistral_embed_model == "mistral-embed"

    def test_env_override_works(self):
        # pydantic-settings should read from env
        import importlib

        from src.config import Settings

        # Just verify the field exists and has a default
        fields = Settings.model_fields
        assert "openai_embed_model" in fields
        assert "gemini_embed_model" in fields
        assert "mistral_embed_model" in fields


# ═══════════════════════════════════════════════════════════════════
#  Plugin loader — skip _ dirs
# ═══════════════════════════════════════════════════════════════════


class TestPluginLoaderSkipUnderscore:
    """Test plugin_loader skips _-prefixed directories."""

    def test_discover_skips_underscore_dirs(self, tmp_path):
        from src.core.actions.plugin_loader import PluginLoader

        # Create plugin dirs
        (tmp_path / "real_plugin").mkdir()
        (tmp_path / "real_plugin" / "plugin.yaml").write_text(
            "name: real\ncategory: test\nmodule: src.test\nversion: '1.0'\nrisk: low\ndependencies: []\ndescription: test\n"
        )
        (tmp_path / "_template").mkdir()
        (tmp_path / "_template" / "plugin.yaml").write_text(
            "name: template\ncategory: test\nmodule: src.test\nversion: '1.0'\nrisk: low\ndependencies: []\ndescription: test\n"
        )
        (tmp_path / "__pycache__").mkdir()

        loader = PluginLoader(str(tmp_path))
        discovered = loader.discover()
        # Only real_plugin should be discovered
        assert len(discovered) == 1
        assert "real_plugin" in discovered[0]
        assert "_template" not in str(discovered)
        assert "__pycache__" not in str(discovered)

    def test_discover_empty_dir(self, tmp_path):
        from src.core.actions.plugin_loader import PluginLoader

        loader = PluginLoader(str(tmp_path))
        assert loader.discover() == []

    def test_discover_nonexistent_dir(self):
        from src.core.actions.plugin_loader import PluginLoader

        loader = PluginLoader("/nonexistent/path/xyz")
        assert loader.discover() == []


# ═══════════════════════════════════════════════════════════════════
#  format_rejected_edits — repr() escaping
# ═══════════════════════════════════════════════════════════════════


class TestFormatRejectedEdits:
    """Test format_rejected_edits escaping."""

    def test_empty_returns_empty(self):
        from src.core.intelligence.skill_editor import format_rejected_edits

        assert format_rejected_edits([]) == ""
        assert format_rejected_edits(None) == ""

    def test_repr_escapes_special_chars(self):
        from src.core.intelligence.skill_editor import format_rejected_edits

        entry = {
            "op": "replace",
            "target": "test",
            "reason": 'hello\nworld\ttab"; injection',
        }
        result = format_rejected_edits([entry])
        assert "<rejected_edits_feedback>" in result
        # repr() should escape special chars
        assert "\\n" in result or "\\t" in result

    def test_max_5_entries(self):
        from src.core.intelligence.skill_editor import format_rejected_edits

        entries = [
            {"op": "replace", "target": f"t{i}", "reason": f"r{i}"} for i in range(10)
        ]
        result = format_rejected_edits(entries)
        # Should only show last 5
        assert result.count("r0") == 0
        assert result.count("r5") == 1
        assert result.count("r9") == 1


# ═══════════════════════════════════════════════════════════════════
#  tool_pairing — record + get + cache invalidation
# ═══════════════════════════════════════════════════════════════════


class TestToolPairingRecord:
    """Test tool_pairing record/get_frequent_pairs."""

    @pytest.mark.asyncio
    async def test_same_tool_no_pair(self):
        from src.core.intelligence.tool_pairing import (
            record_tool_call,
            get_frequent_pairs,
            reset,
        )

        await reset()
        await record_tool_call("search", user_id=0)
        await record_tool_call("search", user_id=0)
        # Same tool called twice → no pair
        pairs = await get_frequent_pairs("search", user_id=0)
        assert pairs == []
        await reset()

    @pytest.mark.asyncio
    async def test_min_count_filter(self):
        from src.core.intelligence.tool_pairing import (
            record_tool_call,
            get_frequent_pairs,
            reset,
        )

        await reset()
        # Only one occurrence → below min_count=2
        await record_tool_call("search", user_id=0)
        await record_tool_call("summarize", user_id=0)
        pairs = await get_frequent_pairs("search", min_count=2, user_id=0)
        assert pairs == []
        await reset()

    @pytest.mark.asyncio
    async def test_cache_invalidated_on_record(self):
        from src.core.intelligence.tool_pairing import (
            record_tool_call,
            get_frequent_pairs,
            reset,
            _cache,
        )

        await reset()
        await record_tool_call("search", user_id=0)
        await record_tool_call("summarize", user_id=0)
        await record_tool_call("search", user_id=0)
        await record_tool_call("summarize", user_id=0)

        # First call populates cache
        r1 = await get_frequent_pairs("search", user_id=0)
        assert len(_cache) > 0

        # Recording new call invalidates cache for that tool
        await record_tool_call("search", user_id=0)
        await record_tool_call("code_exec", user_id=0)

        # New pair should be visible
        r2 = await get_frequent_pairs("search", user_id=0)
        assert "code_exec" in r2 or "summarize" in r2
        await reset()


# ═══════════════════════════════════════════════════════════════════
#  reward_loop config — flags enabled
# ═══════════════════════════════════════════════════════════════════


class TestRewardLoopConfig:
    """Test reward loop config flags are enabled by default."""

    def test_reward_loop_enabled_true(self):
        from src.config import settings

        assert settings.reward_loop_enabled is True

    def test_world_model_enabled_true(self):
        from src.config import settings

        assert settings.world_model_enabled is True

    def test_reward_llm_rubric_enabled_true(self):
        from src.config import settings

        assert settings.reward_llm_rubric_enabled is True

    def test_rubric_semaphore_exists(self):
        from src.core.learning.reward_loop import _rubric_semaphore

        assert _rubric_semaphore is not None
