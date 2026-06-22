"""Система доставки для CronJob.

Поддерживаемые каналы:
- notification_queue — через существующую систему уведомлений
- telegram — прямой send_message через aiogram bot
- userbot — через Telethon клиент (для отправки в ЛС контактов)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.core.infra.text_sanitizer import sanitize_html

logger = logging.getLogger(__name__)

# ponytail: uses shared notifier bot, was lazy singleton
_DISPATCH_TIMEOUT = 30.0  # секунд на канал доставки


async def dispatch_cron_job(
    job_id: int,
    user_id: int,
    payload_type: str,
    payload: str | None,
    channel: str,
) -> dict[str, Any]:
    """Отправить выполнение cron-задачи по указанному каналу.

    Args:
        job_id: ID задачи.
        user_id: Telegram ID пользователя-владельца.
        payload_type: Тип действия ('message', 'llm_prompt', 'webhook').
        payload: JSON-строка с параметрами.
        channel: Канал доставки.

    Returns:
        Словарь с результатом: {"success": bool, "output": str}.
    """
    parsed_payload: dict[str, Any] = {}
    if payload:
        try:
            parsed_payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            parsed_payload = {"text": payload}

    if channel == "notification_queue":
        return await asyncio.wait_for(
            _deliver_via_notification_queue(
                user_id, job_id, payload_type, parsed_payload
            ),
            timeout=_DISPATCH_TIMEOUT,
        )
    elif channel == "telegram":
        return await asyncio.wait_for(
            _deliver_via_telegram(user_id, parsed_payload),
            timeout=_DISPATCH_TIMEOUT,
        )
    elif channel == "userbot":
        return await asyncio.wait_for(
            _deliver_via_userbot(user_id, parsed_payload),
            timeout=_DISPATCH_TIMEOUT,
        )
    else:
        logger.warning("CronDelivery: неизвестный канал %r", channel)
        return {"success": False, "output": f"Неизвестный канал: {channel}"}


async def _deliver_via_notification_queue(
    user_id: int,
    job_id: int,
    payload_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Доставка через notification_queue (существующая система уведомлений).

    Подходит для:
    - "message" — текст уведомления
    - "llm_prompt" — результат LLM генерации
    """
    try:
        from src.core.scheduling.notification_queue import notification_queue

        text = payload.get("text", "")
        if not text:
            text = f"⏰ Cron-задача #{job_id} выполнена"

        topic = f"cron_job_{job_id}"

        await notification_queue.enqueue(
            topic=topic,
            text=text,
            metadata={"cron_job_id": job_id, "user_id": user_id},
        )
        return {"success": True, "output": f"Уведомление отправлено: {text[:100]}"}
    except ImportError:
        logger.warning("CronDelivery: notification_queue недоступен")
        return {"success": False, "output": "notification_queue недоступен"}
    except Exception:
        logger.exception("CronDelivery: ошибка notification_queue")
        return {"success": False, "output": "Ошибка доставки через notification_queue"}


async def _deliver_via_telegram(
    user_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Прямая отправка через Telegram Bot API.

    Использует общий bot из notifier — не создаёт новый
    aiohttp ClientSession на каждый вызов, избегая утечки
    TCP-соединений.

    Безопасность: текст по умолчанию отправляется без parse_mode (plain text).
    Всегда применяется sanitize_html() для предотвращения HTML-инъекций.
    """
    try:
        from src.core.infra.notifier import notifier

        bot = notifier.get_bot()
        if bot is None:
            return {"success": False, "output": "aiogram Bot недоступен"}

        raw_text = payload.get("text", "⏰ Cron-задача выполнена")
        chat_id = payload.get("chat_id", user_id)
        parse_mode = payload.get("parse_mode")  # None = безопасный plain-text default

        # Санитизация текста: всегда пропускаем через sanitize_html()
        # для предотвращения HTML-инъекций через cron-задачи.
        text = sanitize_html(raw_text)

        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
        )

        return {"success": True, "output": f"Сообщение отправлено в чат {chat_id}"}
    except ImportError:
        logger.warning("CronDelivery: aiogram Bot недоступен")
        return {"success": False, "output": "aiogram Bot недоступен"}
    except Exception:
        # НЕ логируем полный traceback — aiogram может включить bot_token
        # в URL HTTP-запроса внутри исключения.
        logger.error(
            "CronDelivery: ошибка отправки Telegram (user_id=%d, chat_id=%s)",
            user_id,
            payload.get("chat_id", user_id),
        )
        return {"success": False, "output": "Ошибка отправки Telegram-сообщения"}


async def _deliver_via_userbot(
    user_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Отправка через Telethon userbot.

    payload ожидает:
        - text: str — текст сообщения
        - contact: str (опционально) — имя контакта
        - peer_id: int (опционально) — peer ID
    """
    try:
        from src.config import settings
        from src.core.infra.userbot_gateway import get_userbot_gateway

        client = get_userbot_gateway().get_client(settings.owner_telegram_id)
        if client is None:
            return {"success": False, "output": "Userbot недоступен"}

        text = payload.get("text", "⏰ Cron-задача выполнена")
        contact = payload.get("contact")
        peer_id = payload.get("peer_id")

        if peer_id:
            await client.send_message(int(peer_id), text)
            return {"success": True, "output": f"Отправлено peer #{peer_id}"}
        elif contact:
            entity = await client.get_entity(contact)
            await client.send_message(entity, text)
            return {"success": True, "output": f"Отправлено контакту {contact}"}
        else:
            # Отправляем самому себе
            await client.send_message("me", text)
            return {"success": True, "output": "Отправлено в Избранное"}
    except ImportError:
        logger.warning("CronDelivery: userbot недоступен")
        return {"success": False, "output": "userbot недоступен"}
    except Exception:
        logger.exception("CronDelivery: ошибка userbot")
        return {"success": False, "output": "Ошибка отправки через userbot"}
