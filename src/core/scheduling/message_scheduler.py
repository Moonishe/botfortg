"""Фоновый цикл отправки отложенных сообщений через userbot."""

import asyncio
import logging

from src.core.infra.task_manager import task_manager

logger = logging.getLogger(__name__)

# ── Защита от наложения (overlap guard) ──
# Если предыдущий тик scheduled_messages_loop ещё не завершился — пропускаем,
# чтобы избежать двойной отправки отложенных сообщений.
_overlap_guard = asyncio.Lock()

SCHEDULED_TICK_SECONDS = 30


async def _check_once(client=None) -> None:
    """Проверяет pending-сообщения и отправляет их через переданный клиент.

    client: Telethon-клиент (или None если userbot недоступен).
    Бизнес-логика чистая: не зависит от src.userbot — клиент получает извне.
    """
    if client is None:
        return  # userbot не запущен, пробуем в следующем тике

    from src.db.repos.scheduled_repo import get_pending, mark_failed, mark_sent
    from src.db.session import get_session

    async with get_session() as session:
        pending = await get_pending(session)

        if not pending:
            return

        for msg in pending:
            try:
                # Находим контакт по имени
                entity = None
                try:
                    entity = await client.get_entity(msg.contact_name)
                except Exception:
                    # Пробуем поискать среди чатов (ограничиваем 200 диалогами)
                    dialogs_checked = 0
                    async for dialog in client.iter_dialogs():
                        dialogs_checked += 1
                        if (
                            dialog.name
                            and msg.contact_name.lower() in dialog.name.lower()
                        ):
                            entity = dialog.entity
                            break
                        if dialogs_checked >= 200:
                            break  # не перебираем 2000+ диалогов

                if entity is None:
                    await mark_failed(
                        session, msg.id, f"Контакт не найден: {msg.contact_name}"
                    )
                    continue

                # Приводим entity к InputPeer через get_input_entity
                target = await client.get_input_entity(entity)  # type: ignore[arg-type]
                await client.send_message(target, msg.text)
                await mark_sent(session, msg.id)
                logger.info(
                    "Scheduled message sent to %s: %s",
                    msg.contact_name,
                    msg.text[:50],
                )

                await asyncio.sleep(1)  # Пауза между сообщениями

            except Exception as e:
                await mark_failed(session, msg.id, str(e)[:500])
                logger.warning("Failed to send scheduled message %s: %s", msg.id, e)

        await session.commit()


@task_manager.task("scheduled-messages-loop")
async def scheduled_messages_loop() -> None:
    """Точка входа: получает клиент userbot и передаёт в чистую бизнес-логику."""
    while True:
        # Защита от наложения: если предыдущий тик ещё не завершён — пропускаем
        if _overlap_guard.locked():
            await asyncio.sleep(SCHEDULED_TICK_SECONDS)
            continue
        await _overlap_guard.acquire()
        try:
            from src.config import settings
            from src.core.infra.userbot_gateway import get_userbot_gateway

            client = get_userbot_gateway().get_client(settings.owner_telegram_id)
            if client is None:
                logger.debug("Userbot not available, skipping scheduled tick")
            await _check_once(client)
        except Exception:
            logger.exception("scheduled-messages tick failed")
        finally:
            _overlap_guard.release()
        await asyncio.sleep(SCHEDULED_TICK_SECONDS)
