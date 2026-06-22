"""Авто-синхронизация контактов и архивного статуса. Настраивается в /settings.

NOTE: get_or_create_user() вызывается при каждом тике цикла для получения
актуальных настроек. Для single-user бота это лёгкий SELECT — накладные расходы
минимальны. Повторный вызов get_or_create_user внутри одного тика устранён —
используется результат первого запроса.
"""

import asyncio
import logging

from src.config import settings
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.core.infra.userbot_gateway import get_userbot_gateway


logger = logging.getLogger(__name__)


DEFAULT_SYNC_INTERVAL_SEC = 7200

# overlap guard: предотвращает параллельный запуск auto-sync
_overlap_guard = asyncio.Lock()

from src.core.infra.task_manager import task_manager


@task_manager.task("auto-sync")
async def auto_sync_loop() -> None:
    while True:
        if _overlap_guard.locked():
            await asyncio.sleep(settings.auto_sync_fallback_sec)
            continue
        async with _overlap_guard:
            try:
                async with get_session() as session:
                    owner = await get_or_create_user(
                        session, settings.owner_telegram_id
                    )
                    enabled = owner.settings.auto_sync_enabled
                    interval_sec = max(
                        30, getattr(owner.settings, "auto_sync_interval_sec", 7200)
                    )
                    # Сохраняем данные до выхода из сессии — повторный get_or_create_user
                    # не требуется, используем атрибуты из уже загруженного owner.
                    owner_tg_id = owner.telegram_id

                if not enabled:
                    await asyncio.sleep(settings.auto_sync_fallback_sec)
                    continue

                client = get_userbot_gateway().get_client(owner_tg_id)
                if client is not None:
                    # NOTE: owner передан из первого get_or_create_user — без повторного
                    # fetch'а. sync_dialogs принимает detached User (использует только
                    # .id и .telegram_id — простые атрибуты, доступные вне сессии).
                    stats = await get_userbot_gateway().sync_dialogs(
                        client, owner, limit=500
                    )
                    logger.info("auto-sync done: %s", stats)

                await asyncio.sleep(interval_sec)
            except Exception:
                logger.exception("auto-sync tick failed")
                await asyncio.sleep(settings.auto_sync_fallback_sec)
