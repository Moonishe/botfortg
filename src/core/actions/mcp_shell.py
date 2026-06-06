"""mcp_shell tool — registered via @tool decorator.

Run terminal commands on the server with safety checks.

Features:
- ``action="run"`` — executes a command via ``subprocess.run`` (30s timeout).
- ``action="check"`` — dry-run, reports what would run without executing.
- **Command allowlist** — only safe/readonly commands are permitted.
- **Shell metacharacter rejection** — ``|``, ``;``, ``&&``, ``>``, ``<`` etc. are blocked.
- **Timeout** — 30 seconds max per command to prevent hanging.
- **Output cap** — stdout/stderr truncated at 10 KB each to prevent DoS.
- Safety: ``shell=False`` + ``shlex.split()`` — shell injection impossible.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import subprocess
from typing import Any

from src.core.actions.tool_registry import ToolActionSpec, tool

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

_SHELL_TIMEOUT = 30  # seconds
_MAX_STDOUT_CHARS = 10000  # ~10 KB cap

# ── Command allowlist ───────────────────────────────────────────────────────

# Allowed command patterns as token tuples (checked with startswith).
# Only commands matching these patterns (by first N tokens) are permitted.
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
}

# Shell metacharacters that are rejected even with shell=False
# (defense-in-depth — they have no special meaning in non‑shell mode).
_DANGEROUS_CHARS: set[str] = {"|", ";", "&&", "||", ">", "<", "`", "$("}


def _is_command_allowed(command: str) -> tuple[bool, str]:
    """Check *command* against the allowlist.

    Returns ``(ok, reason)`` where *ok* is ``True`` if the command is
    allowed, and *reason* describes the rejection when *ok* is ``False``.
    """
    # 1. Scan for dangerous shell metacharacters (defense-in-depth).
    for char in _DANGEROUS_CHARS:
        if char in command:
            return False, f"Command contains dangerous character {char!r}"

    # 2. Parse the command into tokens.
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return False, f"Cannot parse command: {exc}"

    if not tokens:
        return False, "Empty command"

    # 3. Check against allowed patterns (prefix match).
    for pattern in _ALLOWED_COMMANDS:
        if len(tokens) >= len(pattern) and tokens[: len(pattern)] == list(pattern):
            return True, ""

    allowed_bases = sorted({p[0] for p in _ALLOWED_COMMANDS})
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
        "executing.\n"
        "Supports two actions:\n"
        "- 'run' — executes the command via subprocess and returns "
        "stdout/stderr/returncode.\n"
        "- 'check' — dry-run, reports what would run without executing.\n"
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
    },
    params={
        "action": "str — 'run' or 'check'",
        "command": "str — shell command to execute or check",
    },
)
async def mcp_shell(
    action: str,
    command: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Shell command execution tool.

    Args:
        action: ``"run"`` or ``"check"``.
        command: Shell command to execute or check.

    Returns:
        A dict with ``stdout``, ``stderr``, ``returncode`` on success,
        or an ``"error"`` key on failure.
    """
    try:
        if action not in ("run", "check"):
            return {"error": f"Unknown action {action!r}. Valid actions: run, check"}

        if not command or not command.strip():
            return {"error": "command parameter is required"}

        command = command.strip()

        # ── Allowlist validation ───────────────────────────────────────
        allowed, reason = _is_command_allowed(command)
        if not allowed:
            logger.warning("Blocked command %r: %s", command, reason)
            return {"error": reason}

        if action == "check":
            return {
                "ok": True,
                "action": "check",
                "command": command.strip(),
                "message": f"Would execute: {command.strip()}",
            }

        if not bool(kwargs.get("_confirmed", False)):
            return {"error": "requires confirmation"}
        return await _run_command(command.strip())
    except Exception as exc:
        logger.exception("mcp_shell(%r) failed", action)
        return {"error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════
# Implementation
# ══════════════════════════════════════════════════════════════════════════


async def _run_command(command: str) -> dict[str, Any]:
    """Execute *command* in a subprocess (threaded)."""

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
        truncated = bool(result.stdout and len(result.stdout) > _MAX_STDOUT_CHARS)

        return {
            "ok": True,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
            "truncated": truncated,
        }

    return await loop.run_in_executor(None, _do_run)
