"""MCP-инструмент для управления реакциями (эмодзи) на сообщения в Telegram.

Использует userbot (Telethon SendReactionRequest) для отправки реакций.
Поддерживаемые реакции: 👍 👎 ❤️ 🔥 😁 🎉 🤔 👀 💯 🙏
"""

from __future__ import annotations

import logging
from typing import Any

from telethon import TelegramClient
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)

# Поддерживаемые реакции (эмодзи)
_SUPPORTED_REACTIONS: frozenset[str] = frozenset(
    {
        "\U0001f44d",  # 👍
        "\U0001f44e",  # 👎
        "\u2764\ufe0f",  # ❤️
        "\U0001f525",  # 🔥
        "\U0001f601",  # 😁
        "\U0001f389",  # 🎉
        "\U0001f914",  # 🤔
        "\U0001f440",  # 👀
        "\U0001f4af",  # 💯
        "\U0001f64f",  # 🙏
    }
)

# Человекочитаемые имена для ошибок
_REACTION_NAMES: dict[str, str] = {
    "\U0001f44d": "👍",
    "\U0001f44e": "👎",
    "\u2764\ufe0f": "❤️",
    "\U0001f525": "🔥",
    "\U0001f601": "😁",
    "\U0001f389": "🎉",
    "\U0001f914": "🤔",
    "\U0001f440": "👀",
    "\U0001f4af": "💯",
    "\U0001f64f": "🙏",
}


@tool(
    name="react_to_message",
    description=(
        "Поставить реакцию (эмодзи) на сообщение в Telegram. "
        "Поддерживаемые реакции: \U0001f44d \U0001f44e \u2764\ufe0f "
        "\U0001f525 \U0001f601 \U0001f389 \U0001f914 \U0001f440 \U0001f4af \U0001f64f"
    ),
    category="chat",
    risk="low",
    params={
        "chat_id": "int — ID чата, где находится сообщение",
        "message_id": "int — ID сообщения, на которое ставится реакция",
        "reaction": (
            "str — эмодзи реакции "
            "(\U0001f44d, \U0001f44e, \u2764\ufe0f, "
            "\U0001f525, \U0001f601, \U0001f389, "
            "\U0001f914, \U0001f440, \U0001f4af, \U0001f64f)"
        ),
    },
)
async def react_to_message(
    chat_id: int,
    message_id: int,
    reaction: str,
    user=None,
    **kwargs: Any,
) -> dict:
    """Поставить реакцию (эмодзи) на сообщение в Telegram.

    Использует userbot (Telethon SendReactionRequest) для отправки.

    Args:
        chat_id: ID чата.
        message_id: ID сообщения.
        reaction: Эмодзи реакции.
        user: Telegram ID владельца (int или User ORM-объект).

    Returns:
        Словарь с результатом или ошибкой.
    """
    # Валидация реакции
    reaction = reaction.strip()
    if reaction not in _SUPPORTED_REACTIONS:
        supported_str = ", ".join(_REACTION_NAMES.values())
        return {
            "error": (
                f"Неподдерживаемая реакция {reaction!r}. Допустимые: {supported_str}"
            )
        }

    # Получить клиент userbot
    userbot_manager = kwargs.get("userbot_manager")
    if userbot_manager is None:
        return {"error": "userbot_manager не доступен в kwargs"}

    # Нормализовать user -> telegram_id
    if user is None:
        _user_val = kwargs.get("user", 0)
    else:
        _user_val = user

    if hasattr(_user_val, "telegram_id"):
        telegram_id: int = _user_val.telegram_id
    else:
        telegram_id = int(_user_val)

    client: TelegramClient | None = userbot_manager.get_client(telegram_id)
    if client is None:
        return {
            "error": (
                "Нет активного Telegram-клиента для этого пользователя. "
                "Выполните /login."
            )
        }

    # Получить InputPeer чата и отправить реакцию
    try:
        input_peer = await client.get_input_entity(chat_id)
    except Exception as exc:
        logger.exception("Не удалось получить entity для chat_id=%d", chat_id)
        return {"error": f"Не удалось найти чат {chat_id}: {exc}"}

    try:
        await client(
            SendReactionRequest(
                peer=input_peer,
                msg_id=message_id,
                reaction=[ReactionEmoji(emoticon=reaction)],
            )
        )
    except Exception as exc:
        logger.exception(
            "Ошибка отправки реакции chat_id=%d message_id=%d reaction=%r",
            chat_id,
            message_id,
            reaction,
        )
        return {"error": f"Ошибка отправки реакции: {exc}"}

    logger.info(
        "Реакция %s поставлена на сообщение %d в чате %d",
        _REACTION_NAMES.get(reaction, reaction),
        message_id,
        chat_id,
    )

    return {
        "ok": True,
        "chat_id": chat_id,
        "message_id": message_id,
        "reaction": _REACTION_NAMES.get(reaction, reaction),
    }
