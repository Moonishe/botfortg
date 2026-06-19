"""Sandbox isolation via Docker containers.

Provides :class:`SandboxManager` for executing commands inside disposable
or session-scoped Docker containers with resource limits and network isolation.
"""

from src.core.sandbox.manager import SandboxManager

__all__ = ["SandboxManager"]
