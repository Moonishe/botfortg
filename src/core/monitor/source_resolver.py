"""Разрешение Telegram-сущностей: @username, t.me/ ссылки, числовые ID."""

from __future__ import annotations

import logging
import re

from telethon import TelegramClient, types

logger = logging.getLogger(__name__)

# Паттерны для разбора t.me/ ссылок
_RE_TME_USERNAME = re.compile(r"^https?://t\.me/([a-zA-Z][\w]{3,31})(?:/\d+)?/?$")
_RE_TME_CHANNEL = re.compile(r"^https?://t\.me/c/(-?\d+)(?:/\d+)?/?$")
_RE_TME_INVITE = re.compile(r"^https?://t\.me/\+([\w-]+)$")
_RE_NUMERIC = re.compile(r"^-?\d+$")


async def resolve_source(client: TelegramClient, identifier: str) -> dict:
    """Разрешает Telegram-сущность из @username, t.me/ ссылки или числового ID.

    Args:
        client: Активный Telethon-клиент (должен быть подключён и авторизован).
        identifier: Строка идентификатора:
            - @username → разрешается через client.get_entity("@username")
            - https://t.me/username → username из ссылки → get_entity
            - https://t.me/c/123456/789 → ID канала из ссылки
            - -1001234567890 → PeerChannel(id) → get_entity
            - https://t.me/+invite → ImportChatInviteRequest(hash)

    Returns:
        Словарь:
            {"entity_id": int,    # Raw ID сущности (может быть отрицательным)
             "type": str,         # "channel", "group", "supergroup", "chat"
             "title": str,        # Название
             "username": str|None # @username или None}

    Raises:
        ValueError: Если сущность не найдена или недоступна.
    """
    identifier = identifier.strip().rstrip("/")

    # ── t.me/+invite → инвайт-ссылка ──
    m_invite = _RE_TME_INVITE.match(identifier)
    if m_invite:
        hash_str = m_invite.group(1)
        try:
            from telethon.tl.functions.messages import ImportChatInviteRequest

            result = await client(ImportChatInviteRequest(hash_str))
            if isinstance(result, types.ChatInviteAlready):
                chat = result.chat
            elif hasattr(result, "chats") and result.chats:  # type: ignore[union-attr]
                chat = result.chats[0]  # type: ignore[union-attr]
            else:
                raise ValueError(
                    "Не удалось присоединиться по инвайт-ссылке. "
                    "Возможно, ссылка устарела или вы уже в чате."
                )
            return entity_to_dict(chat)
        except Exception as e:
            logger.exception("ImportChatInviteRequest failed for hash=%s", hash_str)
            raise ValueError(f"Ошибка входа по инвайт-ссылке: {e}") from e

    # ── t.me/c/... → ID канала из ссылки ──
    m_channel = _RE_TME_CHANNEL.match(identifier)
    if m_channel:
        channel_id = int(m_channel.group(1))
        return await _resolve_by_id(client, channel_id)

    # ── t.me/username → username из ссылки ──
    m_username = _RE_TME_USERNAME.match(identifier)
    if m_username:
        username = m_username.group(1)
        return await _resolve_by_username(client, username)

    # ── @username ──
    if identifier.startswith("@"):
        username = identifier[1:]
        return await _resolve_by_username(client, username)

    # ── Чисто числовой ID ──
    if _RE_NUMERIC.match(identifier):
        entity_id = int(identifier)
        return await _resolve_by_id(client, entity_id)

    # ── Последняя попытка: может быть username без @ ──
    if re.match(r"^[a-zA-Z][\w]{3,31}$", identifier):
        return await _resolve_by_username(client, identifier)

    raise ValueError(
        f"Не удалось распознать идентификатор: {identifier!r}. "
        "Ожидается @username, https://t.me/username, t.me/+invite или числовой ID."
    )


async def _resolve_by_username(client: TelegramClient, username: str) -> dict:
    """Разрешает сущность по username."""
    try:
        entity = await client.get_entity(username)
    except ValueError:
        raise ValueError(
            f"Пользователь/канал @{username} не найден. Проверь правильность имени."
        ) from None
    except Exception as e:
        logger.exception("get_entity failed for @%s", username)
        raise ValueError(f"Ошибка получения @{username}: {e}") from e

    return entity_to_dict(entity)


async def _resolve_by_id(client: TelegramClient, entity_id: int) -> dict:
    """Разрешает сущность по числовому ID."""
    try:
        from telethon.tl.types import PeerChannel, PeerChat, PeerUser

        # Определяем тип peer по ID
        if entity_id < 0 or entity_id > 1_000_000_000_000:
            peer = PeerChannel(entity_id)
        elif entity_id > 0:
            peer = PeerChat(entity_id)
        else:
            peer = PeerUser(entity_id)

        entity = await client.get_entity(peer)
    except ValueError:
        raise ValueError(
            f"Сущность с ID {entity_id} не найдена. "
            "Возможно, бот/юзербот не состоит в этом чате/канале."
        ) from None
    except Exception as e:
        logger.exception("get_entity failed for id=%d", entity_id)
        raise ValueError(f"Ошибка получения сущности {entity_id}: {e}") from e

    return entity_to_dict(entity)


def entity_to_dict(entity) -> dict:
    """Преобразует Telethon-сущность в словарь для сохранения."""
    is_channel = isinstance(entity, types.Channel)
    is_group = isinstance(entity, types.Chat)

    if is_channel:
        if entity.megagroup:
            etype = "supergroup"
        elif entity.gigagroup:
            etype = "supergroup"
        else:
            etype = "channel"
    elif is_group:
        etype = "group"
    else:
        etype = "chat"

    return {
        "entity_id": entity.id,
        "type": etype,
        "title": getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or str(entity.id),
        "username": getattr(entity, "username", None),
        "access_hash": getattr(entity, "access_hash", None),
    }
