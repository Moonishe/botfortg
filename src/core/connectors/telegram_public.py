"""Коннектор для чтения публичных Telegram-каналов без авторизации."""

from __future__ import annotations

import logging
import re
from typing import Any, cast
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl.types import Channel, Document, Message as TgMessage

from .base import (
    ConnectorActionAnnotations,
    ConnectorActionSpec,
    ConnectorResult,
    ConnectorRuntime,
    ConnectorSpec,
)
from .registry import ConnectorRegistry

logger = logging.getLogger(__name__)


# ── Регистрация в реестре ──────────────────────────────────────────────


def register_telegram_public(registry: ConnectorRegistry) -> None:
    """Зарегистрировать коннектор telegram_public."""
    spec = _telegram_public_spec()
    if registry.get(spec.name) is None:
        registry.register(spec, _telegram_public_handler)


# ── Spec ────────────────────────────────────────────────────────────────


def _telegram_public_spec() -> ConnectorSpec:
    return ConnectorSpec(
        name="telegram_public",
        description=(
            "Чтение публичных Telegram-каналов через внутреннюю "
            "Telethon-сессию. Авторизация не требуется."
        ),
        category="social",
        auth_mode="none",
        docs_url="https://docs.telethon.dev/en/stable/",
        capabilities=(
            "search_channels",
            "read_channel",
            "read_post",
        ),
        actions=(
            ConnectorActionSpec(
                name="search_channels",
                description=(
                    "Поиск публичных каналов по названию или username "
                    "среди диалогов пользователя."
                ),
                risk="low",
                annotations=ConnectorActionAnnotations(
                    title="Поиск каналов", read_only=True
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Название канала или username для поиска.",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 10,
                            "description": "Максимальное количество результатов.",
                        },
                    },
                    "required": ["query"],
                },
            ),
            ConnectorActionSpec(
                name="read_channel",
                description=(
                    "Прочитать последние посты публичного канала "
                    "по @username или ссылке t.me/..."
                ),
                risk="low",
                annotations=ConnectorActionAnnotations(
                    title="Чтение канала", read_only=True
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "@username канала или ссылка t.me/username.",
                        },
                        "id": {
                            "type": "string",
                            "description": (
                                "Числовой ID канала (альтернатива username)."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "default": 20,
                            "description": "Количество последних постов.",
                        },
                    },
                },
            ),
            ConnectorActionSpec(
                name="read_post",
                description=(
                    "Прочитать конкретный пост по ссылке "
                    "t.me/channel/123 или по ID сообщения."
                ),
                risk="low",
                annotations=ConnectorActionAnnotations(
                    title="Чтение поста", read_only=True
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Ссылка вида t.me/channel/123 или https://t.me/channel/123.",
                        },
                        "id": {
                            "type": "string",
                            "description": (
                                "ID сообщения "
                                "(требуется, если указан channel_id вместо url)."
                            ),
                        },
                        "channel": {
                            "type": "string",
                            "description": (
                                "@username или ID канала (если не указан url)."
                            ),
                        },
                    },
                },
            ),
        ),
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _limit(params: dict[str, Any], default: int = 10, maximum: int = 50) -> int:
    try:
        value = int(params.get("limit") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(maximum, value))


def _parse_telegram_url(value: str) -> tuple[str, int | None]:
    """Извлечь username канала и ID сообщения из ссылки t.me/...

    Returns:
        (channel_username, message_id | None)
    """
    value = value.strip()
    if not value:
        return "", None

    # @username
    if value.startswith("@"):
        return value[1:], None

    # t.me/username/123 или https://t.me/username/123
    parsed = urlparse(value)
    if parsed.netloc in ("t.me", "telegram.me", "telegram.dog"):
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return "", None
        username = parts[0]
        msg_id = None
        if len(parts) >= 2 and parts[1].isdigit():
            msg_id = int(parts[1])
        return username, msg_id

    # Просто username без @
    if re.match(r"^[A-Za-z][A-Za-z0-9_]{3,30}$", value):
        return value, None

    # Числовой ID
    if value.lstrip("-").isdigit():
        return value, None

    return "", None


def _format_post(msg: TgMessage) -> dict[str, Any]:
    """Форматировать сообщение Telegram в словарь."""
    msg_text: str = getattr(msg, "text", None) or getattr(msg, "message", None) or ""
    result: dict[str, Any] = {
        "id": msg.id,
        "date": msg.date.isoformat() if msg.date else None,
        "text": msg_text,
    }

    if msg.views is not None:
        result["views"] = msg.views
    if msg.forwards is not None:
        result["forwards"] = msg.forwards
    if msg.replies is not None:
        result["replies_count"] = getattr(msg.replies, "replies", 0)

    # Информация о медиа
    if msg.media is not None:
        media_info = _describe_media(msg)
        if media_info:
            result["media"] = media_info

    return result


def _describe_media(msg: TgMessage) -> dict[str, Any] | None:
    """Описать вложение сообщения."""
    from telethon.tl.types import (
        MessageMediaDocument,
        MessageMediaPhoto,
        MessageMediaWebPage,
    )

    media = msg.media
    if isinstance(media, MessageMediaPhoto):
        photo = media.photo
        if photo:
            return {"type": "photo", "has_stickers": bool(media.spoiler)}
        return {"type": "photo"}
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        if doc is not None and isinstance(doc, Document):
            for attr in doc.attributes:
                from telethon.tl.types import (
                    DocumentAttributeAudio,
                    DocumentAttributeVideo,
                )

                if isinstance(attr, DocumentAttributeVideo):
                    return {
                        "type": "video",
                        "duration": attr.duration,
                        "width": attr.w,
                        "height": attr.h,
                    }
                if isinstance(attr, DocumentAttributeAudio):
                    return {
                        "type": "audio",
                        "duration": attr.duration,
                        "title": getattr(attr, "title", None),
                        "performer": getattr(attr, "performer", None),
                    }
            name = next(
                (
                    getattr(attr, "file_name", None)
                    for attr in doc.attributes
                    if hasattr(attr, "file_name")
                ),
                None,
            )
            return {"type": "document", "filename": name, "size": doc.size}
        return {"type": "document"}
    if isinstance(media, MessageMediaWebPage):
        web = media.webpage
        if web:
            return {
                "type": "webpage",
                "url": getattr(web, "url", None),
                "title": getattr(web, "title", None),
                "description": getattr(web, "description", None),
            }
        return {"type": "webpage"}
    return None


def _get_any_client(runtime: ConnectorRuntime) -> TelegramClient | None:
    """Получить любой активный Telethon-клиент из runtime или синглтона."""
    # 1. Пробуем через runtime.userbot_manager
    mgr = runtime.userbot_manager
    if mgr is not None:
        if hasattr(mgr, "_clients"):
            clients = mgr._clients
            if clients:
                return next(iter(clients.values()))
        if hasattr(mgr, "get_client"):
            client = mgr.get_client(0)  # пробуем telegram_id=0
            if client is not None:
                return client

    # 2. Fallback — синглтон
    try:
        from src.userbot import get_userbot_manager

        singleton = get_userbot_manager()
        if singleton is not None:
            clients = getattr(singleton, "_clients", {})
            if clients:
                return next(iter(clients.values()))
    except Exception:
        logger.debug("userbot_manager singleton not available", exc_info=True)

    return None


# ── Handler ─────────────────────────────────────────────────────────────


async def _telegram_public_handler(
    action: str,
    params: dict[str, Any],
    runtime: ConnectorRuntime,
) -> ConnectorResult:
    """Обработчик действий коннектора telegram_public."""

    client = _get_any_client(runtime)
    if client is None:
        return ConnectorResult(
            False,
            error=(
                "Нет активной Telethon-сессии. "
                "Выполните /login для использования Telegram-коннектора."
            ),
        )

    if action == "search_channels":
        return await _do_search_channels(client, params)

    if action == "read_channel":
        return await _do_read_channel(client, params)

    if action == "read_post":
        return await _do_read_post(client, params)

    return ConnectorResult(
        False, error=f"Неподдерживаемое действие telegram_public: {action}"
    )


async def _do_search_channels(
    client: TelegramClient, params: dict[str, Any]
) -> ConnectorResult:
    query = str(params.get("query") or "").strip().lower()
    if not query:
        return ConnectorResult(False, error="Параметр query обязателен.")
    limit = _limit(params)

    try:
        dialogs = await client.get_dialogs(limit=200)
    except RPCError as exc:
        return ConnectorResult(
            False,
            error=f"Ошибка получения диалогов: {exc.__class__.__name__}",
        )

    results: list[dict[str, Any]] = []
    for dialog in dialogs:
        entity = dialog.entity
        if not isinstance(entity, Channel):
            continue

        title = (getattr(entity, "title", "") or "").lower()
        username = (getattr(entity, "username", "") or "").lower()

        if query in title or query in username:
            results.append(
                {
                    "id": entity.id,
                    "title": getattr(entity, "title", ""),
                    "username": getattr(entity, "username", None),
                    "participants_count": getattr(entity, "participants_count", None),
                    "broadcast": getattr(entity, "broadcast", False),
                }
            )
            if len(results) >= limit:
                break

    return ConnectorResult(
        True,
        data={
            "results": results,
            "query": query,
            "total_scanned": len(dialogs),
        },
    )


async def _do_read_channel(
    client: TelegramClient, params: dict[str, Any]
) -> ConnectorResult:
    value = str(params.get("url") or params.get("id") or "").strip()
    if not value:
        return ConnectorResult(
            False, error="Укажите username канала (url) или числовой ID (id)."
        )
    limit = _limit(params, default=20)

    # Разбираем ссылку/username
    channel_ref, _msg_id = _parse_telegram_url(value)
    if not channel_ref:
        # Возможно, это чистый числовой ID
        if value.lstrip("-").isdigit():
            channel_ref = value
        else:
            return ConnectorResult(
                False,
                error=(
                    "Не удалось распознать канал. "
                    "Укажите @username или ссылку t.me/username."
                ),
            )

    try:
        entity = await client.get_entity(channel_ref)
    except ValueError:
        return ConnectorResult(
            False, error=f"Канал не найден: {channel_ref}. Проверьте username."
        )
    except RPCError as exc:
        return ConnectorResult(
            False,
            error=f"Ошибка получения канала {channel_ref}: {exc.__class__.__name__}",
        )

    channel_info = _describe_channel(entity)

    try:
        messages = await client.get_messages(entity, limit=limit)
    except RPCError as exc:
        return ConnectorResult(
            False, error=f"Ошибка чтения сообщений: {exc.__class__.__name__}"
        )

    if messages is None:
        messages = []
    elif not isinstance(messages, list):
        messages = [messages]

    posts = [_format_post(msg) for msg in messages if msg is not None]

    return ConnectorResult(
        True,
        data={
            "channel": channel_info,
            "posts": posts,
            "count": len(posts),
        },
    )


async def _do_read_post(
    client: TelegramClient, params: dict[str, Any]
) -> ConnectorResult:
    url = str(params.get("url") or "").strip()
    msg_id_str = str(params.get("id") or "").strip()
    channel_str = str(params.get("channel") or "").strip()

    channel_ref: str = ""
    msg_id: int | None = None

    if url:
        channel_ref, msg_id = _parse_telegram_url(url)
        if not channel_ref:
            return ConnectorResult(
                False, error="Не удалось распознать ссылку. Ожидается t.me/channel/123."
            )
    elif channel_str and msg_id_str:
        channel_ref = channel_str
        try:
            msg_id = int(msg_id_str)
        except (TypeError, ValueError):
            return ConnectorResult(
                False, error="id сообщения должен быть целым числом."
            )
    else:
        return ConnectorResult(
            False,
            error="Укажите url (t.me/channel/123) или channel + id.",
        )

    if msg_id is None:
        return ConnectorResult(
            False,
            error="Не указан ID сообщения. Используйте ссылку вида t.me/channel/123.",
        )

    # Разбираем channel_ref (может быть username или числовым ID)
    if not channel_ref.lstrip("-").isdigit():
        # username — нужно получить entity
        try:
            entity = cast(Channel, await client.get_entity(channel_ref))
            channel_ref = str(entity.id)
        except ValueError:
            return ConnectorResult(False, error=f"Канал не найден: {channel_ref}.")
        except RPCError as exc:
            return ConnectorResult(
                False,
                error=f"Ошибка получения канала: {exc.__class__.__name__}",
            )

    try:
        entity = await client.get_entity(int(channel_ref))
    except ValueError:
        return ConnectorResult(False, error=f"Канал с ID {channel_ref} не найден.")
    except RPCError as exc:
        return ConnectorResult(
            False,
            error=f"Ошибка получения канала: {exc.__class__.__name__}",
        )

    try:
        msg = await client.get_messages(entity, ids=msg_id)
    except RPCError as exc:
        return ConnectorResult(
            False,
            error=f"Ошибка чтения сообщения: {exc.__class__.__name__}",
        )

    if msg is None:
        return ConnectorResult(
            False,
            error=(
                f"Пост {msg_id} не найден "
                f"в канале {getattr(entity, 'title', channel_ref)}."
            ),
        )

    return ConnectorResult(
        True,
        data={
            "channel": _describe_channel(entity),
            "post": _format_post(cast(TgMessage, msg)),
        },
    )


def _describe_channel(entity: object) -> dict[str, Any]:
    """Описать канал (entity)."""
    return {
        "id": getattr(entity, "id", None),
        "title": getattr(entity, "title", ""),
        "username": getattr(entity, "username", None),
        "broadcast": getattr(entity, "broadcast", False),
        "participants_count": getattr(entity, "participants_count", None),
    }
