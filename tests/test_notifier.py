"""Tests for src/core/infra/notifier.py."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.infra.notifier import Notifier


@pytest.fixture
def notifier():
    """Fresh Notifier instance for each test."""
    return Notifier()


@pytest.mark.asyncio
async def test_buffer_before_attach(notifier: Notifier):
    await notifier.notify("hello", parse_mode="HTML")
    assert len(notifier._buffer) == 1
    assert notifier._bot is None


@pytest.mark.asyncio
async def test_flush_after_attach(notifier: Notifier):
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    notifier.attach(bot)
    await notifier.notify("hello", parse_mode="HTML")
    assert len(notifier._buffer) == 0
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] is not None
    assert kwargs["text"] == "hello"


@pytest.mark.asyncio
async def test_html_sanitization(notifier: Notifier):
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    notifier.attach(bot)
    await notifier.notify("<script>alert(1)</script>", parse_mode="HTML")
    text = bot.send_message.await_args.kwargs["text"]
    assert "<script>" not in text
    assert "alert(1)" in text


@pytest.mark.asyncio
async def test_empty_text_ignored(notifier: Notifier):
    bot = AsyncMock()
    notifier.attach(bot)
    await notifier.notify("   ", parse_mode="HTML")
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_bot(notifier: Notifier):
    bot = AsyncMock()
    notifier.attach(bot)
    assert notifier.get_bot() is bot
