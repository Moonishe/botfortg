"""SandboxManager — per-session Docker sandbox container lifecycle."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

_CONTAINER_NAME_PREFIX = "telegramhelper-sandbox"
_DOCKER_CLI = "docker"
_CONTAINER_NAME_INVALID_RE = re.compile(r"[^a-zA-Z0-9_.-]")


def _container_name(session_id: str | None) -> str:
    """Build deterministic, Docker-valid container name."""
    safe = _CONTAINER_NAME_INVALID_RE.sub("", session_id or "")
    return f"{_CONTAINER_NAME_PREFIX}-{safe or 'global'}"


class SandboxManager:
    """Manages per-session Docker sandbox containers.

    Containers are created lazily on first ``exec()`` call and kept alive
    with ``sleep infinity``. Resource limits (CPU, memory, network, pids)
    are applied at container creation from ``Settings``.

    Usage::

        manager = SandboxManager(settings)
        if await manager.is_available():
            result = await manager.exec(["python", "-c", "print(42)"])
            print(result["stdout"])  # "42"
    """

    def __init__(self, sandbox_settings: Settings | None = None) -> None:
        if sandbox_settings is None:
            from src.config import settings as _settings

            sandbox_settings = _settings
        self._settings = sandbox_settings
        self._tracked: set[str] = set()  # container names already ensured this session
        self._locks: dict[str, asyncio.Lock] = {}  # per-container-name creation locks
        self._locks_guard = asyncio.Lock()  # protects _locks dict itself

    # ── Public API ──────────────────────────────────────────────────────

    async def is_available(self) -> bool:
        """Check that Docker CLI is reachable and the sandbox image exists."""
        try:
            proc = await asyncio.create_subprocess_exec(
                _DOCKER_CLI,
                "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=8.0)
            if proc.returncode != 0:
                return False
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return False

        # Check image
        image = self._settings.sandbox_image
        try:
            proc = await asyncio.create_subprocess_exec(
                _DOCKER_CLI,
                "image",
                "inspect",
                image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=8.0)
            return proc.returncode == 0
        except (asyncio.TimeoutError, OSError):
            return False

    async def exec(
        self,
        command: list[str],
        *,
        session_id: str | None = None,
        timeout: int | None = None,
        stdin: str | None = None,
    ) -> dict[str, Any]:
        """Execute *command* inside the sandbox container.

        On first call for a given *session_id*, the container is created
        with resource limits from ``Settings``.

        Args:
            command: Command and arguments as a list (e.g. ``["python", "-c", "..."]``).
            session_id: Scope identifier; ``None`` uses the global sandbox.
            timeout: Max seconds; defaults to ``settings.sandbox_timeout``.
            stdin: Optional string to pipe to the command's stdin.

        Returns:
            ``{"stdout": str, "stderr": str, "returncode": int}``.
        """
        if not command:
            return {
                "stdout": "",
                "stderr": "Empty command — no command to execute",
                "returncode": -1,
            }

        name = _container_name(session_id)
        effective_timeout = (
            timeout if timeout is not None else self._settings.sandbox_timeout
        )

        try:
            await self._ensure_container(name)
        except (RuntimeError, FileNotFoundError, OSError) as exc:
            logger.warning("Sandbox container %r unavailable: %s", name, exc)
            return {
                "stdout": "",
                "stderr": f"Sandbox container unavailable: {exc}",
                "returncode": -1,
            }

        docker_cmd = [_DOCKER_CLI, "exec", "-i", name, *command]

        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(
                    input=stdin.encode("utf-8", errors="replace")
                    if stdin is not None
                    else None
                ),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            # Escalation: SIGTERM → SIGKILL → wait once
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
            # Clean up transport to avoid zombie processes
            try:
                transport = getattr(proc, "_transport", None)
                if transport is not None:
                    # ponytail: real asyncio transport.close() is sync;
                    # mocks may return a coroutine — await if needed.
                    close_result = transport.close()
                    if asyncio.iscoroutine(close_result):
                        await close_result
            except Exception:
                pass
            return {
                "stdout": "",
                "stderr": f"Command timed out after {effective_timeout}s",
                "returncode": -1,
            }

        return {
            "stdout": stdout_bytes.decode("utf-8", errors="replace")
            if stdout_bytes
            else "",
            "stderr": stderr_bytes.decode("utf-8", errors="replace")
            if stderr_bytes
            else "",
            "returncode": proc.returncode if proc.returncode is not None else -1,
        }

    async def cleanup(self, session_id: str | None = None) -> None:
        """Remove sandbox container(s).

        Args:
            session_id: If ``None``, removes **all** tracked containers.
        """
        if session_id is not None:
            names = {_container_name(session_id)}
        else:
            names = set(self._tracked)

        for name in list(names):
            await self._rm_container(name)
            self._tracked.discard(name)

        # Clean up per-container locks to prevent unbounded growth
        async with self._locks_guard:
            for name in list(names):
                self._locks.pop(name, None)

    # ── Internal helpers ────────────────────────────────────────────────

    async def _ensure_container(self, name: str) -> None:
        """Create the sandbox container if it is not already running.

        Uses a per-container-name asyncio.Lock to prevent concurrent
        ``exec()`` calls from racing to create the same container.
        """
        if name in self._tracked:
            # Quick path — already confirmed this session.
            return

        # Per-container-name lock prevents two concurrent exec() calls
        # from both passing the _tracked check and racing to create.
        async with self._locks_guard:
            lock = self._locks.get(name)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[name] = lock

        async with lock:
            # Re-check after acquiring the lock — another task may have
            # created the container while we were waiting.
            if name in self._tracked:
                return

            # Double-check via docker inspect.
            running = await self._is_running(name)
            if running:
                self._tracked.add(name)
                return

            # Remove stale stopped/exited container (just in case).
            await self._rm_container(name)

            await self._create_container(name)
            self._tracked.add(name)

    async def _is_running(self, name: str) -> bool:
        """Check via ``docker inspect`` whether *name* is running."""
        try:
            proc = await asyncio.create_subprocess_exec(
                _DOCKER_CLI,
                "inspect",
                "-f",
                "{{.State.Running}}",
                name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            return proc.returncode == 0 and stdout.decode().strip() == "true"
        except (asyncio.TimeoutError, OSError):
            return False

    async def _create_container(self, name: str) -> None:
        """Run ``docker run -d --rm ... sleep infinity``."""
        s = self._settings
        cmd = [
            _DOCKER_CLI,
            "run",
            "-d",
            "--rm",
            "--network",
            "none" if s.sandbox_network_disabled else "bridge",
            "--memory",
            s.sandbox_memory_limit,
            "--cpus",
            str(s.sandbox_cpu_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "50",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64M",
            "--name",
            name,
        ]

        # Optional workspace mount.
        ws = s.sandbox_workspace_access
        if ws in ("ro", "rw"):
            data_dir = str(s.data_dir.resolve())
            mount_opt = "ro" if ws == "ro" else "rw"
            cmd.extend(["-v", f"{data_dir}:/workspace:{mount_opt}"])

        cmd.extend([s.sandbox_image, "sleep", "infinity"])

        logger.info("Creating sandbox container %r: %s", name, " ".join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except FileNotFoundError:
            raise RuntimeError(
                f"Docker CLI ({_DOCKER_CLI!r}) not found. "
                "Install Docker or disable sandbox_enabled."
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Timed out waiting for Docker container {name!r} to start. "
                "Check Docker daemon health and image availability."
            )
        except OSError as exc:
            raise RuntimeError(
                f"Docker daemon unavailable or OS error: {exc}. "
                "Ensure Docker daemon is running."
            )

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            # ponytail: image pull is manual — user must pre-pull.
            raise RuntimeError(
                f"Failed to create sandbox container {name!r}: {err or 'unknown error'}. "
                f"Ensure image {s.sandbox_image!r} is pulled and Docker is running."
            )

        container_id = stdout.decode().strip()
        logger.info("Sandbox container %r created (id=%s)", name, container_id[:12])

    async def _rm_container(self, name: str) -> None:
        """Force-remove *name* if it exists (ignores errors)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                _DOCKER_CLI,
                "rm",
                "-f",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (asyncio.TimeoutError, OSError):
            logger.warning("Failed to remove sandbox container %r", name, exc_info=True)
