"""Verify MultiKeyProvider.close() is NOT called per failed attempt during key rotation.

Per-attempt close() was removed (ponytail) to avoid repeated close/recreate overhead.
This test ensures the invariant holds: provider instances created inside _try_with_retry
are never closed — lifecycle is left to the caller.
"""

from __future__ import annotations

import asyncio

import pytest

from src.llm.base import ChatMessage, TaskType


_close_calls: list[str] = []


class _CloseTracker:
    """Provider that raises RuntimeError on first key, succeeds on second,
    and tracks close()."""

    @staticmethod
    def reset() -> None:
        _close_calls.clear()

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def chat(
        self, messages, *, heavy: bool = False, task_type: str = "default"
    ) -> str:
        if self.api_key == "bad-1":
            raise RuntimeError("rate limit")
        return f"ok:{self.api_key}"

    async def close(self) -> None:
        _close_calls.append(self.api_key)


@pytest.fixture(autouse=True)
def _init_and_cleanup():
    """Initialize mocks, ensure locks, and reset tracker state."""
    _CloseTracker.reset()

    import src.llm.provider_manager as pm

    if not pm._locks_initialized:
        pm._PROVIDER_METRICS_LOCK = asyncio.Lock()
        pm._CIRCUIT_BREAKERS_LOCK = asyncio.Lock()
        pm._PURPOSE_SEMAPHORES = {
            "main": asyncio.Semaphore(2),
            "draft": asyncio.Semaphore(1),
            "memory": asyncio.Semaphore(1),
            "background": asyncio.Semaphore(3),
            "analysis": asyncio.Semaphore(1),
            "urgent": asyncio.Semaphore(2),
            "search": asyncio.Semaphore(2),
            "summarize": asyncio.Semaphore(2),
            "fallback": asyncio.Semaphore(2),
        }
        pm._locks_initialized = True
    yield


@pytest.mark.asyncio
async def test_multikey_chat_never_calls_provider_close():
    """MultiKeyProvider.chat() must NOT call provider.close() per failed attempt.

    The first key raises RuntimeError("rate limit") — a retryable error.
    The second key succeeds.  Neither provider instance should have close() called,
    because per-attempt close was intentionally removed to avoid overhead.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.llm.router import MultiKeyProvider

    _CloseTracker.reset()

    # Patch all external side-effects so the test doesn't touch DB or metrics.
    with (
        patch("src.llm.router._track_llm_usage", new_callable=AsyncMock),
        patch("src.llm.router._record_provider_success", new_callable=AsyncMock),
        patch("src.llm.router._record_provider_failure", new_callable=AsyncMock),
        patch(
            "src.llm.router.acquire_purpose_slot",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("src.llm.router.release_purpose_slot", new_callable=MagicMock),
    ):
        provider = MultiKeyProvider("close-test", _CloseTracker, ["bad-1", "good-2"])

        result = await provider.chat(
            [ChatMessage(role="user", content="hi")],
            task_type=TaskType.DEFAULT,
        )

    assert result == "ok:good-2"
    # The critical invariant: close() was never called on any provider instance
    # created during key rotation.
    assert _close_calls == [], (
        f"provider.close() must NOT be called per failed attempt, "
        f"but got: {_close_calls}"
    )

    # The lifecycle is managed by MultiKeyProvider.close() — call it and verify
    # all tracked providers are closed.
    await provider.close()
    assert sorted(_close_calls) == ["bad-1", "good-2"], (
        f"MultiKeyProvider.close() should close tracked providers, "
        f"but got: {_close_calls}"
    )
