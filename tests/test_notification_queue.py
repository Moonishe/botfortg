"""Tests for src/core/scheduling/notification_queue.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.scheduling.notification_queue import NotificationQueue
from src.db.models import Notification


@pytest.mark.usefixtures("_db_init")
class TestNotificationQueue:
    @pytest.fixture
    def queue(self):
        return NotificationQueue()

    @pytest.fixture(autouse=True)
    async def _clean_notifications(self):
        from src.db.session import SessionLocal
        from sqlalchemy import delete

        async with SessionLocal() as session:
            await session.execute(delete(Notification))
            await session.commit()

    @pytest.mark.asyncio
    async def test_enqueue_persists_notification(self, queue):
        notif_id = await queue.enqueue(
            "test", "hello", priority=Notification.PRIORITY_MEDIUM
        )
        assert notif_id > 0

    @pytest.mark.asyncio
    async def test_enqueue_clamps_priority(self, queue):
        notif_id = await queue.enqueue("test", "hello", priority=999)
        assert notif_id > 0

    @pytest.mark.asyncio
    async def test_critical_bypass(self, queue):
        with patch("src.core.scheduling.notification_queue.notifier") as mock_notifier:
            mock_notifier.notify = AsyncMock()
            notif_id = await queue.enqueue(
                "test", "critical", priority=Notification.PRIORITY_CRITICAL
            )
            assert notif_id == 0
            mock_notifier.notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self, queue):
        notif_id = await queue.enqueue(
            "test", "   ", priority=Notification.PRIORITY_MEDIUM
        )
        assert notif_id == 0

    @pytest.mark.asyncio
    async def test_flush_groups(self, queue):
        with patch("src.core.scheduling.notification_queue.notifier") as mock_notifier:
            mock_notifier.notify = AsyncMock()
            await queue.enqueue("t1", "msg1", priority=Notification.PRIORITY_MEDIUM)
            await queue.enqueue("t1", "msg2", priority=Notification.PRIORITY_MEDIUM)
            flushed = await queue.flush()
            assert flushed == 2
            mock_notifier.notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, queue):
        from datetime import datetime, timedelta, UTC
        from src.db.session import SessionLocal

        notif_id = await queue.enqueue(
            "test", "old", priority=Notification.PRIORITY_MEDIUM
        )
        async with SessionLocal() as session:
            notif = await session.get(Notification, notif_id)
            notif.created_at = datetime.now(UTC) - timedelta(hours=25)
            await session.commit()
        cleaned = await queue.cleanup_expired()
        assert cleaned == 1
