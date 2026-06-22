import asyncio
import logging
import time

from src.config import settings
from src.core.infra.task_manager import task_manager

logger = logging.getLogger(__name__)

# overlap guards: предотвращают параллельный запуск фоновых циклов
_disk_monitor_guard = asyncio.Lock()
_media_sweep_guard = asyncio.Lock()
_global_style_guard = asyncio.Lock()
_instruction_optimizer_guard = asyncio.Lock()


@task_manager.task("disk-monitor")
async def disk_monitor_loop() -> None:
    import shutil
    from src.core.scheduling.notification_queue import notification_queue

    last_warning_at = 0.0

    while True:
        if _disk_monitor_guard.locked():
            await asyncio.sleep(settings.disk_monitor_interval_sec)
            continue
        async with _disk_monitor_guard:
            try:
                usage = shutil.disk_usage(settings.data_dir)
                free_mb = usage.free / (1024 * 1024)

                if free_mb < settings.disk_critical_mb:
                    await notification_queue.enqueue(
                        topic="disk_monitor",
                        text=f"⛔ КРИТИЧНО: свободно {free_mb:.0f} MB на диске!",
                        priority=0,
                    )
                elif free_mb < settings.disk_warning_mb:
                    now = time.monotonic()
                    if now - last_warning_at > 3600:
                        await notification_queue.enqueue(
                            topic="disk_monitor",
                            text=f"⚠️ Мало места на диске: {free_mb:.0f} MB свободно.",
                            priority=1,
                        )
                        last_warning_at = now
            except Exception:
                logger.exception("disk monitor error")
        await asyncio.sleep(settings.disk_monitor_interval_sec)


@task_manager.task("media-sweep")
async def media_sweep_loop() -> None:
    from src.core.contacts.chat_service import sweep_orphaned_media
    from src.db.models import Notification

    await asyncio.sleep(60)
    while True:
        if _media_sweep_guard.locked():
            await asyncio.sleep(6 * 3600)
            continue
        async with _media_sweep_guard:
            try:
                deleted = await sweep_orphaned_media()
                if deleted:
                    from src.core.scheduling.notification_queue import (
                        notification_queue,
                    )

                    await notification_queue.enqueue(
                        topic="media_sweep",
                        text=f"🧹 Очищено {deleted} временных медиа-файлов.",
                        priority=Notification.PRIORITY_LOW,
                    )
            except Exception:
                logger.exception("media sweep loop error")
        await asyncio.sleep(6 * 3600)


@task_manager.task("global-style")
async def global_style_scheduler_loop() -> None:
    from src.core.contacts.style_profile import update_global_style_profile

    oid = settings.owner_telegram_id
    while True:
        if _global_style_guard.locked():
            await asyncio.sleep(settings.global_style_interval_sec)
            continue
        async with _global_style_guard:
            try:
                await update_global_style_profile(oid)
            except Exception:
                logger.exception("Global style update failed")
        await asyncio.sleep(settings.global_style_interval_sec)


@task_manager.task("instruction-optimizer")
async def instruction_optimizer_scheduler_loop() -> None:
    from src.core.intelligence.instruction_optimizer import instruction_optimizer

    oid = settings.owner_telegram_id
    while True:
        if _instruction_optimizer_guard.locked():
            await asyncio.sleep(settings.instruction_optimizer_interval_sec)
            continue
        async with _instruction_optimizer_guard:
            try:
                await instruction_optimizer.instruction_optimizer_loop(oid)
            except Exception:
                logger.exception("Instruction optimizer failed")
        await asyncio.sleep(settings.instruction_optimizer_interval_sec)
