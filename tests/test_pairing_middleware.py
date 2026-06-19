"""Tests for DM pairing access guard middleware and filters."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Dispatcher, Router
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update, User

from src.bot.filters import OwnerOnly, OwnerOnlyStrict


@pytest.fixture
def owner_id() -> int:
    return 100500


@pytest.fixture
def paired_id() -> int:
    return 200600


@pytest.fixture
def stranger_id() -> int:
    return 300700


@pytest.fixture
def make_message() -> Callable[[int, str], Message]:
    def _make(user_id: int, text: str = "/test") -> Message:
        return Message(
            message_id=1,
            from_user=User(id=user_id, is_bot=False, first_name="User"),
            chat=Chat(id=user_id, type="private"),
            date="2026-01-01T00:00:00",
            text=text,
        )

    return _make


@pytest.fixture
def setup_dp(owner_id: int):
    """Build a minimal dispatcher with the pairing guard and a test handler."""
    from src.bot.app import universal_access_guard
    from src.core.security.pairing import PairingManager

    router = Router(name="test")
    handler = AsyncMock()

    @router.message(Command("test"))
    async def _handler(message: Message) -> None:
        await handler(message)

    router.message.filter(OwnerOnly())

    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(universal_access_guard)
    dp.include_router(router)

    return dp, handler, PairingManager


@pytest.mark.asyncio
async def test_owner_reaches_handler(setup_dp, owner_id: int, make_message) -> None:
    dp, handler, _ = setup_dp
    bot = AsyncMock()
    bot.id = 1
    msg = make_message(owner_id, "/test")
    update = Update(update_id=1, message=msg)

    with patch("src.bot.app.settings.owner_telegram_id", owner_id):
        await dp.feed_update(bot, update)

    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_stranger_gets_pairing_code_and_no_handler(
    setup_dp, stranger_id: int, make_message
) -> None:
    dp, handler, _ = setup_dp
    bot = AsyncMock()
    bot.id = 1
    msg = make_message(stranger_id, "/test")
    update = Update(update_id=1, message=msg)

    with patch("src.bot.app.settings.owner_telegram_id", stranger_id - 1):
        await dp.feed_update(bot, update)

    # Bot should reply with the pairing code (Message.answer calls bot).
    assert bot.called
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_paired_user_reaches_handler(
    setup_dp, paired_id: int, stranger_id: int, make_message
) -> None:
    dp, handler, PM = setup_dp
    bot = AsyncMock()
    bot.id = 1
    msg = make_message(paired_id, "/test")
    update = Update(update_id=1, message=msg)

    pm = PM()
    # Simulate an already-approved paired contact without hitting the DB.
    pm._allowlist.add(paired_id)

    with (
        patch("src.bot.app.pairing", pm),
        patch("src.bot.app.settings.owner_telegram_id", stranger_id),
    ):
        await dp.feed_update(bot, update)

    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_only_filter_allows_paired_user(
    owner_id: int, paired_id: int
) -> None:
    filt = OwnerOnly()
    msg = Message(
        message_id=1,
        from_user=User(id=paired_id, is_bot=False, first_name="Paired"),
        chat=Chat(id=paired_id, type="private"),
        date="2026-01-01T00:00:00",
        text="hi",
    )
    with patch("src.bot.filters.settings.owner_telegram_id", owner_id):
        assert await filt(msg, _paired_user=True) is True


@pytest.mark.asyncio
async def test_owner_only_filter_allows_owner(owner_id: int) -> None:
    filt = OwnerOnly()
    msg = Message(
        message_id=1,
        from_user=User(id=owner_id, is_bot=False, first_name="Owner"),
        chat=Chat(id=owner_id, type="private"),
        date="2026-01-01T00:00:00",
        text="hi",
    )
    with patch("src.bot.filters.settings.owner_telegram_id", owner_id):
        assert await filt(msg) is True


@pytest.mark.asyncio
async def test_owner_only_filter_rejects_stranger(
    owner_id: int, stranger_id: int
) -> None:
    filt = OwnerOnly()
    msg = Message(
        message_id=1,
        from_user=User(id=stranger_id, is_bot=False, first_name="Stranger"),
        chat=Chat(id=stranger_id, type="private"),
        date="2026-01-01T00:00:00",
        text="hi",
    )
    with patch("src.bot.filters.settings.owner_telegram_id", owner_id):
        assert await filt(msg) is False


@pytest.mark.asyncio
async def test_owner_only_strict_rejects_paired_user(
    owner_id: int, paired_id: int
) -> None:
    filt = OwnerOnlyStrict()
    msg = Message(
        message_id=1,
        from_user=User(id=paired_id, is_bot=False, first_name="Paired"),
        chat=Chat(id=paired_id, type="private"),
        date="2026-01-01T00:00:00",
        text="hi",
    )
    with patch("src.bot.filters.settings.owner_telegram_id", owner_id):
        assert await filt(msg) is False


@pytest.mark.asyncio
async def test_owner_only_strict_allows_owner(owner_id: int) -> None:
    filt = OwnerOnlyStrict()
    msg = Message(
        message_id=1,
        from_user=User(id=owner_id, is_bot=False, first_name="Owner"),
        chat=Chat(id=owner_id, type="private"),
        date="2026-01-01T00:00:00",
        text="hi",
    )
    with patch("src.bot.filters.settings.owner_telegram_id", owner_id):
        assert await filt(msg) is True
