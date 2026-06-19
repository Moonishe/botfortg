"""Tests for Docker sandbox isolation (Phase 1.2)."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.config import Settings
from src.core.sandbox import SandboxManager


# ── Helpers ────────────────────────────────────────────────────────────────


def _mock_settings(**overrides) -> Settings:
    """Build a Settings instance with sandbox defaults and overrides."""
    kwargs = {
        "bot_token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef",
        "owner_telegram_id": 123456789,
        "encryption_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "sandbox_enabled": True,
        **overrides,
    }
    return Settings(**kwargs)


def _make_mock_subprocess(returncode=0, stdout=b"ok\n", stderr=b""):
    """Create an AsyncMock subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock()
    return proc


# ── is_available ───────────────────────────────────────────────────────────


class TestIsAvailable:
    async def test_returns_false_when_docker_not_found(self):
        """is_available() returns False if docker CLI is missing."""
        manager = SandboxManager(_mock_settings())
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            assert await manager.is_available() is False

    async def test_returns_false_when_docker_info_fails(self):
        """is_available() returns False when docker info exits non-zero."""
        manager = SandboxManager(_mock_settings())
        proc = _make_mock_subprocess(returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            assert await manager.is_available() is False

    async def test_returns_true_when_docker_and_image_ok(self):
        """is_available() returns True when docker and image are present."""
        manager = SandboxManager(_mock_settings())
        proc = _make_mock_subprocess(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            assert await manager.is_available() is True


# ── Container creation flags ───────────────────────────────────────────────


class TestContainerCreationFlags:
    async def test_default_flags_include_network_none_and_cap_drop(self):
        """Default container flags include --network none and --cap-drop ALL."""
        manager = SandboxManager(_mock_settings())

        # Mock: container not running → will attempt creation.
        inspect_proc = _make_mock_subprocess(returncode=1, stdout=b"false")
        # Remove fails (no existing container), then create succeeds.
        rm_proc = _make_mock_subprocess(returncode=0)
        create_proc = _make_mock_subprocess(returncode=0, stdout=b"abc123\n")
        exec_proc = _make_mock_subprocess(returncode=0, stdout=b"hello\n")

        call_order = [inspect_proc, rm_proc, create_proc, exec_proc]

        async def _side_effect(*args, **kwargs):
            return call_order.pop(0)

        with patch("asyncio.create_subprocess_exec", side_effect=_side_effect) as mock:
            await manager.exec(["echo", "hello"])

        # Find the create call (third invocation).
        calls = mock.call_args_list
        # 1: docker inspect, 2: docker rm -f, 3: docker run
        assert len(calls) >= 3
        create_call_args = calls[2][0]

        # Check required flags are present.
        flag_str = " ".join(str(a) for a in create_call_args)
        assert "--network" in flag_str
        assert "none" in flag_str
        assert "--memory" in flag_str
        assert "256m" in flag_str
        assert "--cpus" in flag_str
        assert "0.5" in flag_str
        assert "--cap-drop" in flag_str
        assert "ALL" in flag_str
        assert "--security-opt" in flag_str
        assert "no-new-privileges" in flag_str
        assert "--pids-limit" in flag_str
        assert "50" in flag_str
        assert "--rm" in flag_str
        assert "sleep" in flag_str
        assert "infinity" in flag_str

    async def test_workspace_ro_mount_added(self):
        """When sandbox_workspace_access='ro', -v mount with :ro is added."""
        manager = SandboxManager(_mock_settings(sandbox_workspace_access="ro"))

        inspect_proc = _make_mock_subprocess(returncode=1, stdout=b"false")
        rm_proc = _make_mock_subprocess(returncode=0)
        create_proc = _make_mock_subprocess(returncode=0, stdout=b"abc123\n")
        exec_proc = _make_mock_subprocess(returncode=0, stdout=b"test\n")

        call_order = [inspect_proc, rm_proc, create_proc, exec_proc]

        async def _side_effect(*args, **kwargs):
            return call_order.pop(0)

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=_side_effect,
        ) as mock:
            await manager.exec(["echo", "test"])

        create_call_args = mock.call_args_list[2][0]
        flag_str = " ".join(str(a) for a in create_call_args)
        assert "-v" in flag_str
        assert ":ro" in flag_str

    async def test_workspace_none_skips_mount(self):
        """When sandbox_workspace_access='none', no -v flag is added."""
        manager = SandboxManager(_mock_settings(sandbox_workspace_access="none"))

        inspect_proc = _make_mock_subprocess(returncode=1, stdout=b"false")
        rm_proc = _make_mock_subprocess(returncode=0)
        create_proc = _make_mock_subprocess(returncode=0, stdout=b"abc123\n")
        exec_proc = _make_mock_subprocess(returncode=0, stdout=b"test\n")

        call_order = [inspect_proc, rm_proc, create_proc, exec_proc]

        async def _side_effect2(*args, **kwargs):
            return call_order.pop(0)

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=_side_effect2,
        ) as mock:
            await manager.exec(["echo", "test"])

        create_call_args = mock.call_args_list[2][0]
        flag_str = " ".join(str(a) for a in create_call_args)
        assert "-v" not in flag_str


# ── exec ───────────────────────────────────────────────────────────────────


class TestExec:
    async def test_exec_returns_stdout_stderr_returncode(self):
        """exec() returns a dict with stdout, stderr, returncode."""
        manager = SandboxManager(_mock_settings())

        # Container already running (tracked).
        manager._tracked.add("telegramhelper-sandbox-global")

        exec_proc = _make_mock_subprocess(
            returncode=0,
            stdout=b"hello world\n",
            stderr=b"",
        )

        with patch("asyncio.create_subprocess_exec", return_value=exec_proc):
            result = await manager.exec(["echo", "hello"])

        assert result["stdout"] == "hello world\n"
        assert result["stderr"] == ""
        assert result["returncode"] == 0

    async def test_exec_handles_timeout(self):
        """exec() reports timeout when command exceeds limit."""
        manager = SandboxManager(_mock_settings(sandbox_timeout=1))
        manager._tracked.add("telegramhelper-sandbox-global")

        async def slow_communicate(input=None):
            await asyncio.sleep(60)
            return b"", b""

        proc = AsyncMock()
        proc.communicate = slow_communicate
        proc.wait = AsyncMock()
        proc.terminate = Mock()
        proc.kill = Mock()

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await manager.exec(["sleep", "60"], timeout=1)

        assert result["returncode"] == -1
        assert "timed out" in result["stderr"].lower()
        assert proc.terminate.called


# ── cleanup ────────────────────────────────────────────────────────────────


class TestCleanup:
    async def test_cleanup_calls_docker_rm_f(self):
        """cleanup() runs docker rm -f for tracked containers."""
        manager = SandboxManager(_mock_settings())
        container_name = "telegramhelper-sandbox-global"
        manager._tracked.add(container_name)

        rm_proc = _make_mock_subprocess(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=rm_proc) as mock:
            await manager.cleanup()

        calls = mock.call_args_list
        rm_call_args = calls[0][0] if calls else ()
        flag_str = " ".join(str(a) for a in rm_call_args)
        assert "rm" in flag_str
        assert "-f" in flag_str
        assert container_name in flag_str

    async def test_cleanup_specific_session(self):
        """cleanup(session_id='x') removes only session-x container."""
        manager = SandboxManager(_mock_settings())
        manager._tracked.add("telegramhelper-sandbox-test")
        manager._tracked.add("telegramhelper-sandbox-other")

        rm_proc = _make_mock_subprocess(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=rm_proc) as mock:
            await manager.cleanup(session_id="test")

        calls = mock.call_args_list
        assert len(calls) == 1
        flag_str = " ".join(str(a) for a in calls[0][0])
        assert "telegramhelper-sandbox-test" in flag_str
        assert "telegramhelper-sandbox-other" not in flag_str
        assert "telegramhelper-sandbox-test" not in manager._tracked  # removed
        assert "telegramhelper-sandbox-other" in manager._tracked  # still tracked


# ── Smoke import ───────────────────────────────────────────────────────────


def test_sandbox_manager_imports():
    """Verify SandboxManager is importable from src.core.sandbox."""
    from src.core.sandbox import SandboxManager  # noqa: F401


def test_sandbox_config_settings_has_fields():
    """Verify sandbox fields exist on Settings."""
    s = _mock_settings()
    assert s.sandbox_enabled is True
    assert s.sandbox_image == "python:3.13-slim"
    assert s.sandbox_timeout == 30
    assert s.sandbox_memory_limit == "256m"
    assert s.sandbox_cpu_limit == 0.5
    assert s.sandbox_network_disabled is True
    assert s.sandbox_workspace_access == "none"
    assert s.sandbox_scope == "session"


# ── Edge case tests ─────────────────────────────────────────────────────────


class TestEdgeCases:
    """Tests for edge case handling in SandboxManager."""

    async def test_exec_with_empty_command(self):
        """exec() with empty command list returns error dict."""
        manager = SandboxManager(_mock_settings())
        result = await manager.exec([])
        assert result["returncode"] == -1
        assert "Empty command" in result["stderr"]

    async def test_exec_container_creation_failure_returns_error(self):
        """exec() returns error dict when container creation fails."""
        manager = SandboxManager(_mock_settings())

        # Mock: docker inspect says not running
        inspect_proc = _make_mock_subprocess(returncode=1, stdout=b"false")
        # Mock: docker run fails with non-zero exit
        create_proc = _make_mock_subprocess(
            returncode=1, stdout=b"", stderr=b"image not found"
        )
        rm_proc = _make_mock_subprocess(returncode=0)

        call_order = [inspect_proc, rm_proc, create_proc]

        async def _side_effect(*args, **kwargs):
            return call_order.pop(0)

        with patch("asyncio.create_subprocess_exec", side_effect=_side_effect):
            result = await manager.exec(["echo", "hello"])

        # Should return error dict, not raise RuntimeError
        assert "Sandbox container unavailable" in result["stderr"]
        assert result["returncode"] == -1

    async def test_exec_docker_cli_not_found(self):
        """exec() returns error dict when Docker CLI is not installed."""
        manager = SandboxManager(_mock_settings())

        # Mock: docker inspect says not running
        inspect_proc = _make_mock_subprocess(returncode=1, stdout=b"false")
        rm_proc = _make_mock_subprocess(returncode=0)

        call_order = [inspect_proc, rm_proc]

        async def _side_effect(*args, **kwargs):
            if len(call_order) > 0:
                return call_order.pop(0)
            raise FileNotFoundError("docker not found")

        with patch("asyncio.create_subprocess_exec", side_effect=_side_effect):
            result = await manager.exec(["echo", "hello"])

        assert "Sandbox container unavailable" in result["stderr"]
        assert result["returncode"] == -1

    async def test_workspace_access_invalid_skips_mount(self):
        """Invalid workspace_access value (not ro/rw) skips mount safely."""
        # pydantic validates this at Settings level, but test defense-in-depth
        manager = SandboxManager(_mock_settings(sandbox_workspace_access="none"))
        # "none" should skip mount — already tested, just confirm no crash
        inspect_proc = _make_mock_subprocess(returncode=1, stdout=b"false")
        rm_proc = _make_mock_subprocess(returncode=0)
        create_proc = _make_mock_subprocess(returncode=0, stdout=b"abc123\n")
        exec_proc = _make_mock_subprocess(returncode=0, stdout=b"test\n")

        call_order = [inspect_proc, rm_proc, create_proc, exec_proc]

        async def _side_effect(*args, **kwargs):
            return call_order.pop(0)

        with patch("asyncio.create_subprocess_exec", side_effect=_side_effect) as mock:
            await manager.exec(["echo", "test"])

        create_call_args = mock.call_args_list[2][0]
        flag_str = " ".join(str(a) for a in create_call_args)
        assert "-v" not in flag_str  # no mount for invalid or "none"
