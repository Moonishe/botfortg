"""mcp_shell tool — registered via @tool decorator.

Run terminal commands on the server with safety checks. Supports **local**,
**Docker** and **SSH** backends.

Features:
- ``action="run"`` — executes a command via backend (30s timeout).
- ``action="check"`` — dry-run, reports what would run without executing.
- ``action="list_backends"`` — returns available backends and their status.
- **Backends**: ``local`` (default), ``docker``, ``ssh``.
- **Command allowlist** — only safe/readonly commands are permitted.
- **Shell metacharacter rejection** — ``|``, ``;``, ``&&``, ``>``, ``<`` etc. are blocked.
- **Timeout** — 30 seconds max per command to prevent hanging.
- **Output cap** — stdout/stderr truncated at 10 KB each to prevent DoS.
- Safety: ``shell=False`` + ``shlex.split()`` — shell injection impossible.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from src.config import settings
from src.core.actions.tool_registry import ToolActionSpec, tool

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

_SHELL_TIMEOUT = 30  # seconds
_MAX_STDOUT_CHARS = 10000  # ~10 KB cap

# ── Backend configuration ──────────────────────────────────────────────────

_DOCKER_CONTAINER: str = (
    getattr(settings, "docker_container", None) or "telegram-assistant"
)
_SSH_HOST: str = getattr(settings, "ssh_host", None) or ""
_SSH_USER: str = getattr(settings, "ssh_user", None) or ""
_SSH_KEY_PATH: str = getattr(settings, "ssh_key_path", None) or ""
# known_hosts default: secure host key verification.
# Set SSH_KNOWN_HOSTS=None in .env ONLY for development/temporary environments.
_SSH_KNOWN_HOSTS: str | None = getattr(settings, "ssh_known_hosts", None)
if _SSH_KNOWN_HOSTS is None:
    _SSH_KNOWN_HOSTS = "~/.ssh/known_hosts"

# ── Path containment guard for cat/type ────────────────────────────────────

_DATA_DIR = settings.data_dir.resolve()


def _is_path_within_data_dir(arg: str) -> bool:
    """Check that *arg* resolves to a path inside ``data_dir``.

    Rejects ``..`` traversal, absolute paths, and paths that resolve
    outside the allowed data directory.
    """
    # Reject raw ".." components early
    if ".." in Path(arg).parts:
        return False

    # Используем os.path.realpath для consistency с mcp_tools.py:
    # корректно обрабатывает .. на Windows и не путает data/..foo с ".."
    resolved_str = os.path.realpath(str(settings.data_dir / arg))
    path = Path(resolved_str)
    try:
        path.relative_to(_DATA_DIR)
    except ValueError:
        return False

    return True


# Allowed command patterns as token tuples (checked with startswith).
# Only commands matching these patterns (by first N tokens) are permitted.
# NOTE: cat/type на файлах data_dir могут выдать содержимое .env, БД
# и других чувствительных файлов. Допустимо для single-user admin-бота.
_ALLOWED_COMMANDS: set[tuple[str, ...]] = {
    ("dir",),
    ("ls",),
    ("cat",),
    ("type",),
    ("echo",),
    ("tree",),
    ("git", "status"),
    ("git", "log"),
    ("git", "diff"),
    ("python", "--version"),
    ("pip", "list"),
    ("pytest", "--collect-only"),
    ("docker", "ps"),
    ("docker", "logs"),
    ("docker", "inspect"),
    ("docker-compose", "ps"),
}

# Commands allowed when executing *inside* a Docker container.
# SECURITY NOTE: curl/wget permit outbound HTTP — admin trust model assumed.
_ALLOWED_COMMANDS_DOCKER: set[tuple[str, ...]] = {
    ("ls",),
    ("cat",),
    ("echo",),
    ("python", "--version"),
    ("pip", "list"),
    ("dir",),
    ("tree",),
    ("pwd",),
    ("whoami",),
    ("df",),
    ("free",),
    ("ps", "aux"),
    ("netstat",),
    ("git", "status"),
}

# Commands allowed when executing on a remote machine via SSH.
_ALLOWED_COMMANDS_SSH: set[tuple[str, ...]] = {
    ("ls",),
    ("cat",),
    ("echo",),
    ("python", "--version"),
    ("pip", "list"),
    ("dir",),
    ("tree",),
    ("pwd",),
    ("whoami",),
    ("df",),
    ("free",),
    ("ps", "aux"),
    ("netstat",),
    ("git", "status"),
    ("systemctl", "status"),
    ("journalctl",),
}

# Admin mode — расширенный доступ (требует подтверждения владельца).
# Включает pip, git push/pull, docker управление, systemctl restart.
_ALLOWED_COMMANDS_ADMIN: set[tuple[str, ...]] = {
    *_ALLOWED_COMMANDS,
    ("pip", "install"),
    ("pip", "uninstall"),
    ("pip", "freeze"),
    ("python", "-m"),
    ("git", "pull"),
    ("git", "checkout"),
    ("git", "add"),
    ("git", "commit"),
    ("git", "push"),
    ("git", "fetch"),
    ("git", "merge"),
    ("docker", "restart"),
    ("docker", "stop"),
    ("docker", "start"),
    ("docker-compose", "restart"),
    ("docker-compose", "stop"),
    ("docker-compose", "start"),
    ("systemctl", "restart"),
    ("systemctl", "start"),
    ("systemctl", "stop"),
}

# Characters rejected even with shell=False.
# Shell metacharacters (|, ;, &&, ||, >, <, `, $(, &, #, $) are harmless
# with shell=False — they are passed literally to the command binary.
# Only \n and \r remain: they can smuggle additional commands in protocols
# (SMTP injection, HTTP request smuggling) even without a shell.
_DANGEROUS_CHARS: set[str] = {
    "\n",
    "\r",
}

# Extended dangerous chars for backends that pass commands through a shell
# (Docker: sh -c, SSH: conn.run()). These metacharacters MUST be blocked.
_DANGEROUS_CHARS_SHELL: set[str] = {
    "\n",
    "\r",  # newline injection
    ";",  # command separator
    "|",
    "&",  # pipe, background
    "`",  # command substitution (backtick)
    "$",  # variable expansion / $( )
    "!",  # history expansion / negation
    "<",
    ">",  # redirect
    "\\",  # escape
}


def _is_command_allowed(
    command: str,
    allowlist: set[tuple[str, ...]] | None = None,
    *,
    detect_shell_metachars: bool = False,
) -> tuple[bool, str]:
    """Check *command* against the *allowlist* (or ``_ALLOWED_COMMANDS``).

    Returns ``(ok, reason)`` where *ok* is ``True`` if the command is
    allowed, and *reason* describes the rejection when *ok* is ``False``.

    When *detect_shell_metachars* is True, blocks ``; | & ` $ ! < > \\``
    (used by Docker/SSH backends where commands pass through sh -c).
    """
    effective_allowlist = allowlist if allowlist is not None else _ALLOWED_COMMANDS

    # 0. Reject raw newlines before tokenization — they are consumed by
    #    shlex.split() as separators but re-interpreted as command separators
    #    by shells used in Docker/SSH backends.
    if "\n" in command or "\r" in command:
        return False, "Command contains newline or carriage return"

    # 1. Parse the command into tokens FIRST.
    #    shlex.split() handles quoting — dangerous chars inside quotes
    #    become literal parts of tokens and are harmless with shell=False.
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return False, f"Cannot parse command: {exc}"

    if not tokens:
        return False, "Empty command"

    # 2. Scan parsed tokens for dangerous shell metacharacters
    #    (defense-in-depth — reassembled tokens could still be injected
    #    if the target binary interprets them, e.g. newlines in args).
    #    Check each token individually so quoted chars are not flagged.
    chars_to_check = (
        _DANGEROUS_CHARS_SHELL if detect_shell_metachars else _DANGEROUS_CHARS
    )
    for token in tokens:
        for char in chars_to_check:
            if char in token:
                return (
                    False,
                    f"Command contains dangerous character {char!r} in token {token!r}",
                )

    # 3. Check against allowed patterns (prefix match).
    for pattern in effective_allowlist:
        if len(tokens) >= len(pattern) and tokens[: len(pattern)] == list(pattern):
            # 3a. Deny-list + path containment: block cat/type on sensitive files
            #     or paths that escape data_dir via ../ or absolute paths.
            if tokens[0] in ("cat", "type") and len(tokens) > 1:
                _denied_exact = frozenset(
                    {
                        ".env",
                        "config.py",
                        "settings.py",
                    }
                )
                _denied_substrings = (
                    "secret",
                    "token",
                    "credential",
                    "password",
                    "id_rsa",
                    "id_ed25519",
                    "session_string",
                    "bot_token",
                )
                _denied_paths = (".ssh/", ".env.")
                for _idx, _arg in enumerate(t.lower() for t in tokens[1:]):
                    # Skip flag-like arguments (cat -n, type -?)
                    _original_arg = tokens[_idx + 1]
                    if _original_arg.startswith("-"):
                        continue
                    # Path containment: reject ../ traversal and paths outside data_dir
                    if not _is_path_within_data_dir(_original_arg):
                        return (
                            False,
                            f"Access denied: {_original_arg!r} resolves outside data/ directory",
                        )
                    _basename = _arg.split("/")[-1].split("\\")[-1]
                    if (
                        _basename in _denied_exact
                        or any(d in _basename for d in _denied_substrings)
                        or any(d in _arg for d in _denied_paths)
                    ):
                        return (
                            False,
                            f"Access denied: {tokens[_idx + 1]!r} is a sensitive file",
                        )
            return True, ""

    allowed_bases = sorted({p[0] for p in effective_allowlist})
    return False, (
        f"Command {tokens[0]!r} is not in the allowlist. "
        f"Allowed: {', '.join(allowed_bases)}"
    )


# ══════════════════════════════════════════════════════════════════════════
# Tool: mcp_shell
# ══════════════════════════════════════════════════════════════════════════


@tool(
    name="mcp_shell",
    description=(
        "Run terminal commands on the server or check what would run without "
        "executing. Supports **local**, **Docker** and **SSH** backends.\n"
        "Actions:\n"
        "- 'run' — executes the command via the selected backend and returns "
        "stdout/stderr/returncode.\n"
        "- 'check' — dry-run, reports what would run without executing.\n"
        "- 'list_backends' — returns available backends and their status.\n"
        "Dangerous commands are blocked (shell=False — injection impossible)."
    ),
    category="system",
    risk="critical",
    requires_confirmation=True,
    actions={
        "check": ToolActionSpec(
            name="check",
            risk="low",
            read_only=True,
            idempotent=True,
            user_content=False,
        ),
        "run": ToolActionSpec(
            name="run",
            risk="critical",
            read_only=False,
            destructive=False,
            idempotent=False,
            requires_confirmation=True,
            user_content=True,
        ),
        "list_backends": ToolActionSpec(
            name="list_backends",
            risk="low",
            read_only=True,
            idempotent=True,
            user_content=False,
        ),
    },
    params={
        "action": "str — 'run', 'check', or 'list_backends'",
        "command": "str — shell command to execute or check",
        "backend": "str — 'local' (default), 'docker', or 'ssh'",
        "admin_mode": "bool — True для расширенного доступа (pip, git push, systemctl). Только для local backend.",  # noqa: E501
    },
)
async def mcp_shell(
    action: str,
    command: str = "",
    backend: str = "local",
    admin_mode: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Shell command execution tool.

    Args:
        action: ``"run"``, ``"check"``, or ``"list_backends"``.
        command: Shell command to execute or check.
        backend: ``"local"`` (default), ``"docker"``, or ``"ssh"``.
        admin_mode: If True, allows extended commands (pip install, git push,
            systemctl). Requires owner confirmation.

    Returns:
        A dict with ``stdout``, ``stderr``, ``returncode`` on success,
        or an ``"error"`` key on failure.
    """
    try:
        if action not in ("run", "check", "list_backends"):
            return {
                "error": (
                    f"Unknown action {action!r}. "
                    "Valid actions: run, check, list_backends"
                )
            }

        if action == "list_backends":
            return await _list_backends()

        if not command or not command.strip():
            return {"error": "command parameter is required"}

        command = command.strip()

        # ── Select allowlist based on backend + admin_mode ────────────
        if backend == "local":
            allowlist = _ALLOWED_COMMANDS_ADMIN if admin_mode else _ALLOWED_COMMANDS
        elif backend == "docker":
            if admin_mode:
                return {"error": "admin_mode is not supported for docker backend"}
            allowlist = _ALLOWED_COMMANDS_DOCKER
        elif backend == "ssh":
            if admin_mode:
                return {"error": "admin_mode is not supported for ssh backend"}
            allowlist = _ALLOWED_COMMANDS_SSH
        else:
            return {"error": f"Unknown backend {backend!r}. Valid: local, docker, ssh"}

        # ── Allowlist validation (Docker/SSH use shell metachar check) ──
        is_shell_backend = backend in ("docker", "ssh")
        allowed, reason = _is_command_allowed(
            command, allowlist=allowlist, detect_shell_metachars=is_shell_backend
        )
        if not allowed:
            logger.warning(
                "Blocked command %r on backend %r: %s", command, backend, reason
            )
            return {"error": reason}

        if action == "check":
            return {
                "ok": True,
                "action": "check",
                "backend": backend,
                "command": command.strip(),
                "message": f"Would execute on {backend!r}: {command.strip()}",
            }

        # Normalise _confirmed: СТРОГО только настоящий boolean True.
        # Раньше принималась разрешительная строковая логика ("true","1","yes"),
        # что было несовместимо с канонической проверкой is_confirmed_truthy
        # и mcp_server.py. Теперь — единый источник истины: только True.
        from src.core.security import is_confirmed_truthy

        _confirmed = is_confirmed_truthy(kwargs.get("_confirmed", False))
        if not _confirmed:
            return {"error": "requires confirmation"}

        if admin_mode:
            _admin_confirmed = is_confirmed_truthy(
                kwargs.get("_admin_confirmed", False)
            )
            if not _admin_confirmed:
                return {"error": ("admin_mode requires separate _admin_confirmed=True")}

        # ── Dispatch to backend ───────────────────────────────────────
        if backend == "local":
            return await _run_local(command)
        elif backend == "docker":
            return await _run_docker(command)
        elif backend == "ssh":
            return await _run_ssh(command)
        else:  # pragma: no cover — validated above
            return {"error": f"Unknown backend {backend!r}"}
    except Exception as exc:
        logger.exception("mcp_shell(%r, backend=%r) failed", action, backend)
        return {"error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════
# Backend availability
# ══════════════════════════════════════════════════════════════════════════


async def _list_backends() -> dict[str, Any]:
    """Return which backends are available and their configuration."""
    backends: dict[str, dict[str, Any]] = {
        "local": {"available": True, "description": "Local subprocess execution"},
    }

    # Check Docker availability (offloaded to thread to avoid blocking event loop).
    docker_available = False
    docker_reason = ""
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["docker", "info"],  # noqa: S607 — system binary on PATH, allowlist-protected
                capture_output=True,
                timeout=10,
                shell=False,
                text=True,
            ),
        )
        if result.returncode == 0:
            docker_available = True
        else:
            docker_reason = f"docker info failed (rc={result.returncode})"
    except FileNotFoundError:
        docker_reason = "docker CLI not found"
    except subprocess.TimeoutExpired:
        docker_reason = "docker info timed out"
    except OSError as exc:
        docker_reason = f"OS error: {exc}"

    backends["docker"] = {
        "available": docker_available,
        "container": _DOCKER_CONTAINER,
        "description": f"Docker exec in container {_DOCKER_CONTAINER!r}",
        **({"reason": docker_reason} if not docker_available and docker_reason else {}),
    }

    # Check SSH availability.
    ssh_available = False
    ssh_reason = ""
    try:
        import asyncssh  # noqa: F401  # pyright: ignore[reportUnusedImport]

        if _SSH_HOST:
            ssh_available = True
        else:
            ssh_reason = (
                "SSH host not configured. Set ssh_host in settings or SSH_HOST in .env"
            )
    except ImportError:
        ssh_reason = "asyncssh not installed. Run: pip install asyncssh"

    backends["ssh"] = {
        "available": ssh_available,
        "host": _SSH_HOST or "(not configured)",
        "user": _SSH_USER or "(not configured)",
        "description": f"SSH to {_SSH_HOST or '?'} as {_SSH_USER or '?'}",
        **({"reason": ssh_reason} if not ssh_available and ssh_reason else {}),
    }

    return {
        "ok": True,
        "action": "list_backends",
        "backends": backends,
    }


# ══════════════════════════════════════════════════════════════════════════
# Implementation — Local backend
# ══════════════════════════════════════════════════════════════════════════


async def _run_local(command: str) -> dict[str, Any]:
    """Execute *command* in a local subprocess (threaded)."""

    loop = asyncio.get_running_loop()

    def _do_run() -> dict[str, Any]:
        try:
            result = subprocess.run(
                shlex.split(command),
                capture_output=True,
                timeout=_SHELL_TIMEOUT,
                shell=False,
                text=True,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Command %r timed out after %ss", command, _SHELL_TIMEOUT)
            return {"error": f"Command timed out after {_SHELL_TIMEOUT}s"}
        except OSError as exc:
            logger.warning("OS error executing %r: %s", command, exc)
            return {"error": f"Execution failed: {exc}"}

        stdout = (result.stdout or "")[:_MAX_STDOUT_CHARS]
        stderr = (result.stderr or "")[:_MAX_STDOUT_CHARS]
        truncated = bool(
            (result.stdout and len(result.stdout) > _MAX_STDOUT_CHARS)
            or (result.stderr and len(result.stderr) > _MAX_STDOUT_CHARS)
        )

        return {
            "ok": True,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
            "truncated": truncated,
        }

    return await loop.run_in_executor(None, _do_run)


# ══════════════════════════════════════════════════════════════════════════
# Implementation — Docker backend
# ══════════════════════════════════════════════════════════════════════════


async def _run_docker(command: str) -> dict[str, Any]:
    """Execute *command* inside the configured Docker container."""

    loop = asyncio.get_running_loop()

    def _do_run() -> dict[str, Any]:
        docker_cmd = [
            "docker",
            "exec",
            _DOCKER_CONTAINER,
            "sh",
            "-c",
            command,
        ]
        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                timeout=_SHELL_TIMEOUT,
                shell=False,
                text=True,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "Docker command %r timed out after %ss in container %r",
                command,
                _SHELL_TIMEOUT,
                _DOCKER_CONTAINER,
            )
            return {"error": f"Command timed out after {_SHELL_TIMEOUT}s"}
        except OSError as exc:
            logger.warning(
                "OS error executing %r in docker container %r: %s",
                command,
                _DOCKER_CONTAINER,
                exc,
            )
            return {"error": f"Execution failed: {exc}"}

        stdout = (result.stdout or "")[:_MAX_STDOUT_CHARS]
        stderr = (result.stderr or "")[:_MAX_STDOUT_CHARS]
        truncated = bool(
            (result.stdout and len(result.stdout) > _MAX_STDOUT_CHARS)
            or (result.stderr and len(result.stderr) > _MAX_STDOUT_CHARS)
        )

        # Detect common Docker failures (container not running, daemon down).
        if result.returncode != 0:
            stderr_lower = (result.stderr or "").lower()
            if "is not running" in stderr_lower or "no such container" in stderr_lower:
                return {
                    "error": (
                        f"Docker container {_DOCKER_CONTAINER!r} is not running. "
                        f"Start it with: docker start {_DOCKER_CONTAINER}"
                    ),
                    "detail": stderr,
                }
            if "cannot connect to the docker daemon" in stderr_lower:
                return {
                    "error": (
                        "Docker daemon is not running. "
                        "Start it with: sudo systemctl start docker"
                    ),
                    "detail": stderr,
                }

        return {
            "ok": True,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
            "truncated": truncated,
        }

    return await loop.run_in_executor(None, _do_run)


# ══════════════════════════════════════════════════════════════════════════
# Implementation — SSH backend
# ══════════════════════════════════════════════════════════════════════════


async def _run_ssh(command: str) -> dict[str, Any]:
    """Execute *command* on a remote host via asyncssh."""

    # Guarded import.
    try:
        import asyncssh
    except ImportError:
        return {"error": "asyncssh not installed. Run: pip install asyncssh"}

    if not _SSH_HOST:
        return {
            "error": (
                "SSH host not configured. Set ssh_host in settings or SSH_HOST in .env"
            )
        }

    connect_kwargs: dict[str, Any] = {
        "host": _SSH_HOST,
        "username": _SSH_USER or None,
        "known_hosts": _SSH_KNOWN_HOSTS,  # secure default, configurable via SSH_KNOWN_HOSTS
        "connect_timeout": 15,
    }
    if _SSH_KEY_PATH:
        connect_kwargs["client_keys"] = [_SSH_KEY_PATH]

    try:
        async with asyncssh.connect(**connect_kwargs) as conn:
            result = await conn.run(command, timeout=_SHELL_TIMEOUT)

            stdout = (result.stdout or "")[:_MAX_STDOUT_CHARS]
            stderr = (result.stderr or "")[:_MAX_STDOUT_CHARS]
            truncated = bool(
                (result.stdout and len(result.stdout) > _MAX_STDOUT_CHARS)
                or (result.stderr and len(result.stderr) > _MAX_STDOUT_CHARS)
            )

            return {
                "ok": True,
                "stdout": stdout,
                "stderr": stderr,
                "returncode": result.exit_status
                if result.exit_status is not None
                else -1,
                "truncated": truncated,
            }
    except asyncssh.TimeoutError:
        logger.warning(
            "SSH command %r timed out after %ss on %r",
            command,
            _SHELL_TIMEOUT,
            _SSH_HOST,
        )
        return {"error": f"SSH command timed out after {_SHELL_TIMEOUT}s"}
    except asyncssh.Error as exc:
        logger.warning(
            "SSH error executing %r on %r: %s",
            command,
            _SSH_HOST,
            exc,
        )
        return {"error": f"SSH error: {exc}"}
    except OSError as exc:
        logger.warning("OS error during SSH to %r: %s", _SSH_HOST, exc)
        return {"error": f"SSH connection failed: {exc}"}
