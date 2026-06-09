"""MCP-инструмент для управления реакциями (эмодзи) на сообщения в Telegram.

Использует userbot (Telethon SendReactionRequest) для отправки реакций.
Работает с ЛЮБЫМИ сообщениями в ЛЮБЫХ чатах (не только ответы боту).
Поддерживает все бесплатные (non-premium) реакции Telegram:
👍 👎 ❤️ 🔥 🥰 👏 😁 🤔 🤯 😱 🤬 😢 🎉 🤩 🤮 💩 🙏 👌 🕊 💡 🤝 🎄 🍾 💋 🗿 🌚 🌭 🏆 💯 🤡 🍓 🍌 🤪 🐳 💔 🤨 🥱 🥴 😍 🤣 🐱 🦄 🙈 🤗

Пустая строка в reaction — удаляет все реакции с сообщения.
"""

from __future__ import annotations

import logging
from typing import Any

from telethon import TelegramClient
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)

# Все бесплатные (non-premium) реакции Telegram (42 шт.)
_SUPPORTED_REACTIONS: frozenset[str] = frozenset(
    {
        "\U0001f44d",  # 👍
        "\U0001f44e",  # 👎
        "\u2764\ufe0f",  # ❤️
        "\U0001f525",  # 🔥
        "\U0001f970",  # 🥰
        "\U0001f44f",  # 👏
        "\U0001f601",  # 😁
        "\U0001f914",  # 🤔
        "\U0001f92f",  # 🤯
        "\U0001f631",  # 😱
        "\U0001f92c",  # 🤬
        "\U0001f622",  # 😢
        "\U0001f389",  # 🎉
        "\U0001f929",  # 🤩
        "\U0001f92e",  # 🤮
        "\U0001f4a9",  # 💩
        "\U0001f64f",  # 🙏
        "\U0001f44c",  # 👌
        "\U0001f54a",  # 🕊
        "\U0001f4a1",  # 💡
        "\U0001f91d",  # 🤝
        "\U0001f384",  # 🎄
        "\U0001f37e",  # 🍾
        "\U0001f48b",  # 💋
        "\U0001f5ff",  # 🗿
        "\U0001f31a",  # 🌚
        "\U0001f32d",  # 🌭
        "\U0001f3c6",  # 🏆
        "\U0001f4af",  # 💯
        "\U0001f921",  # 🤡
        "\U0001f353",  # 🍓
        "\U0001f34c",  # 🍌
        "\U0001f92a",  # 🤪
        "\U0001f433",  # 🐳
        "\U0001f494",  # 💔
        "\U0001f928",  # 🤨
        "\U0001f971",  # 🥱
        "\U0001f974",  # 🥴
        "\U0001f60d",  # 😍
        "\U0001f923",  # 🤣
        "\U0001f431",  # 🐱
        "\U0001f984",  # 🦄
        "\U0001f648",  # 🙈
        "\U0001f917",  # 🤗
    }
)

# Человекочитаемые имена для ошибок и логов
_REACTION_NAMES: dict[str, str] = {
    "\U0001f44d": "👍",
    "\U0001f44e": "👎",
    "\u2764\ufe0f": "❤️",
    "\U0001f525": "🔥",
    "\U0001f970": "🥰",
    "\U0001f44f": "👏",
    "\U0001f601": "😁",
    "\U0001f914": "🤔",
    "\U0001f92f": "🤯",
    "\U0001f631": "😱",
    "\U0001f92c": "🤬",
    "\U0001f622": "😢",
    "\U0001f389": "🎉",
    "\U0001f929": "🤩",
    "\U0001f92e": "🤮",
    "\U0001f4a9": "💩",
    "\U0001f64f": "🙏",
    "\U0001f44c": "👌",
    "\U0001f54a": "🕊",
    "\U0001f4a1": "💡",
    "\U0001f91d": "🤝",
    "\U0001f384": "🎄",
    "\U0001f37e": "🍾",
    "\U0001f48b": "💋",
    "\U0001f5ff": "🗿",
    "\U0001f31a": "🌚",
    "\U0001f32d": "🌭",
    "\U0001f3c6": "🏆",
    "\U0001f4af": "💯",
    "\U0001f921": "🤡",
    "\U0001f353": "🍓",
    "\U0001f34c": "🍌",
    "\U0001f92a": "🤪",
    "\U0001f433": "🐳",
    "\U0001f494": "💔",
    "\U0001f928": "🤨",
    "\U0001f971": "🥱",
    "\U0001f974": "🥴",
    "\U0001f60d": "😍",
    "\U0001f923": "🤣",
    "\U0001f431": "🐱",
    "\U0001f984": "🦄",
    "\U0001f648": "🙈",
    "\U0001f917": "🤗",
}


@tool(
    name="react_to_message",
    description=(
        "Поставить/убрать реакцию (эмодзи) на ЛЮБОЕ сообщение в ЛЮБОМ чате Telegram. "
        "Работает с любыми сообщениями (не только ответы боту). "
        "Пустая строка '' убирает все реакции. "
        "Поддерживаемые реакции: \U0001f44d \U0001f44e \u2764\ufe0f "
        "\U0001f525 \U0001f970 \U0001f44f \U0001f601 \U0001f914 \U0001f92f "
        "\U0001f631 \U0001f92c \U0001f622 \U0001f389 \U0001f929 \U0001f92e "
        "\U0001f4a9 \U0001f64f \U0001f44c \U0001f54a \U0001f4a1 \U0001f91d "
        "\U0001f384 \U0001f37e \U0001f48b \U0001f5ff \U0001f31a \U0001f32d "
        "\U0001f3c6 \U0001f4af \U0001f921 \U0001f353 \U0001f34c \U0001f92a "
        "\U0001f433 \U0001f494 \U0001f928 \U0001f971 \U0001f974 \U0001f60d "
        "\U0001f923 \U0001f431 \U0001f984 \U0001f648 \U0001f917"
    ),
    category="chat",
    risk="low",
    params={
        "chat_id": "int — ID чата, где находится сообщение",
        "message_id": "int — ID сообщения, на которое ставится реакция",
        "reaction": (
            "str — эмодзи реакции (любая бесплатная реакция Telegram). "
            "Пустая строка '' — убрать все реакции с сообщения."
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
    """Поставить/убрать реакцию (эмодзи) на сообщение в Telegram.

    Использует userbot (Telethon SendReactionRequest) для отправки.
    Работает с ЛЮБЫМИ сообщениями в ЛЮБЫХ чатах.

    Args:
        chat_id: ID чата.
        message_id: ID сообщения.
        reaction: Эмодзи реакции. Пустая строка '' — убрать все реакции.
        user: Telegram ID владельца (int или User ORM-объект).

    Returns:
        Словарь с результатом или ошибкой.
    """
    # Удаление всех реакций: пустая строка
    if reaction.strip() == "":
        return await _remove_all_reactions(chat_id, message_id, user, kwargs)

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

    reaction_display = _REACTION_NAMES.get(reaction, reaction)
    logger.info(
        "Реакция %s поставлена на сообщение %d в чате %d",
        reaction_display,
        message_id,
        chat_id,
    )

    return {
        "ok": True,
        "message": f"Поставил {reaction_display} на сообщение {message_id}",
    }


async def _remove_all_reactions(
    chat_id: int,
    message_id: int,
    user: Any,
    kwargs: dict[str, Any],
) -> dict:
    """Убрать все реакции с сообщения (reaction=[] в SendReactionRequest)."""
    userbot_manager = kwargs.get("userbot_manager")
    if userbot_manager is None:
        return {"error": "userbot_manager не доступен в kwargs"}

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
                reaction=[],  # пустой список = удалить все реакции
            )
        )
    except Exception as exc:
        logger.exception(
            "Ошибка удаления реакций chat_id=%d message_id=%d",
            chat_id,
            message_id,
        )
        return {"error": f"Ошибка удаления реакций: {exc}"}

    logger.info(
        "Реакции удалены с сообщения %d в чате %d",
        message_id,
        chat_id,
    )

    return {
        "ok": True,
        "message": f"Убрал реакции с сообщения {message_id}",
    }
