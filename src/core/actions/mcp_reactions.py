"""MCP-инструмент для управления реакциями (эмодзи) на сообщения в Telegram.

Использует userbot (Telethon SendReactionRequest) для отправки реакций.
Работает с ЛЮБЫМИ сообщениями в ЛЮБЫХ чатах (не только ответы боту).
Поддерживает все бесплатные (non-premium) реакции Telegram:
👍 👎 ❤️ 🔥 🥰 👏 😁 🤔 🤯 😱 🤬 😢 🎉 🤩 🤮 💩 🙏 👌 🕊 💡 🤝 🎄 🍾 💋 🗿 🌚 🌭 🏆 💯 🤡 🍓 🍌 🤪 🐳 💔 🤨 🥱 🥴 😍 🤣 🐱 🦄 🙈 🤗

Пустая строка в reaction — удаляет все реакции с сообщения.

Также содержит инструмент find_message для поиска сообщений по имени контакта.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from telethon import TelegramClient
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji, User as TlUser

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

# Алиасы естественного языка → emoji (русский + английский)
# Используется когда LLM передаёт текстовое описание вместо emoji (например "лайк")
_REACTION_ALIASES: dict[str, str] = {
    # Русские алиасы
    "лайк": "\U0001f44d",  # 👍
    "плюс": "\U0001f44d",  # 👍
    "класс": "\U0001f44d",  # 👍
    "дизлайк": "\U0001f44e",  # 👎
    "минус": "\U0001f44e",  # 👎
    "сердце": "\u2764\ufe0f",  # ❤️
    "сердечко": "\u2764\ufe0f",  # ❤️
    "любовь": "\u2764\ufe0f",  # ❤️
    "огонь": "\U0001f525",  # 🔥
    "смех": "\U0001f601",  # 😁
    "смешно": "\U0001f601",  # 😁
    "аплодисменты": "\U0001f44f",  # 👏
    "хлоп": "\U0001f44f",  # 👏
    "браво": "\U0001f44f",  # 👏
    "задумался": "\U0001f914",  # 🤔
    "думаю": "\U0001f914",  # 🤔
    "хм": "\U0001f914",  # 🤔
    "грусть": "\U0001f622",  # 😢
    "грустно": "\U0001f622",  # 😢
    "праздник": "\U0001f389",  # 🎉
    "ура": "\U0001f389",  # 🎉
    "сотня": "\U0001f4af",  # 💯
    "100": "\U0001f4af",  # 💯
    "сотка": "\U0001f4af",  # 💯
    "глаза": "\U0001f440",  # 👀
    "смотрю": "\U0001f440",  # 👀
    "молитва": "\U0001f64f",  # 🙏
    "спасибо": "\U0001f64f",  # 🙏
    "ок": "\U0001f44c",  # 👌
    "окей": "\U0001f44c",  # 👌
    "хорошо": "\U0001f44c",  # 👌
    "клоун": "\U0001f921",  # 🤡
    "кот": "\U0001f431",  # 🐱
    "котик": "\U0001f431",  # 🐱
    "банан": "\U0001f34c",  # 🍌
    "клубника": "\U0001f353",  # 🍓
    "взрыв": "\U0001f92f",  # 🤯
    "шок": "\U0001f631",  # 😱
    "злость": "\U0001f92c",  # 🤬
    "🫶": "\U0001f970",  # 🥰
    # Английские алиасы
    "like": "\U0001f44d",
    "dislike": "\U0001f44e",
    "heart": "\u2764\ufe0f",
    "love": "\u2764\ufe0f",
    "fire": "\U0001f525",
    "laugh": "\U0001f601",
    "clap": "\U0001f44f",
    "think": "\U0001f914",
    "sad": "\U0001f622",
    "party": "\U0001f389",
    "eyes": "\U0001f440",
    "thanks": "\U0001f64f",
    "ok": "\U0001f44c",
    "100": "\U0001f4af",
}

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

    # Разрешить алиас естественного языка → emoji
    reaction = reaction.strip()
    reaction_lower = reaction.lower()
    if reaction not in _SUPPORTED_REACTIONS:
        # Попробовать найти алиас (например "лайк" → "👍")
        resolved = _REACTION_ALIASES.get(reaction_lower)
        if resolved is not None:
            reaction = resolved
            logger.debug("Resolved reaction alias %r → %s", reaction_lower, reaction)

    # Валидация реакции
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


# ── find_message: поиск сообщения по имени контакта ─────────────────────────


@tool(
    name="find_message",
    description=(
        "Найти сообщение от контакта по имени. "
        "Используй перед react_to_message чтобы получить chat_id и message_id. "
        "Возвращает chat_id, message_id, текст и дату сообщения."
    ),
    category="chat",
    risk="low",
    params={
        "contact_name": "str — имя контакта (например «Настя», «Вася»)",
        "position": (
            "str — позиция сообщения: "
            "'last' (последнее), 'first' (первое за сегодня), "
            "или число как строка '1','2','3' (1=последнее, 2=предпоследнее, ...)"
        ),
    },
)
async def find_message(
    contact_name: str,
    position: str = "last",
    user: Any = None,
    **kwargs: Any,
) -> dict:
    """Найти сообщение от контакта по имени.

    Использует contact_resolver для поиска контакта и Telethon userbot
    для получения сообщений из чата.

    Args:
        contact_name: Имя контакта (например «Настя»).
        position: Позиция сообщения — "last", "first", или число как строка.
        user: Telegram ID владельца (int или User ORM-объект).

    Returns:
        {"chat_id": int, "message_id": int, "text": str, "date": str}
        или {"error": str} при ошибке.
    """
    # ── Нормализовать user → telegram_id и ORM-объект ──
    if user is None:
        _user_val = kwargs.get("user", 0)
    else:
        _user_val = user

    if hasattr(_user_val, "telegram_id"):
        telegram_id: int = _user_val.telegram_id
        user_orm = _user_val
    else:
        telegram_id = int(_user_val)
        user_orm = None

    # ── Получить userbot-клиент ──
    userbot_manager = kwargs.get("userbot_manager")
    if userbot_manager is None:
        return {"error": "userbot_manager не доступен в kwargs"}

    client: TelegramClient | None = userbot_manager.get_client(telegram_id)
    if client is None:
        return {
            "error": (
                "Нет активного Telegram-клиента для этого пользователя. "
                "Выполните /login."
            )
        }

    # ── Разрешить контакт через существующий contact_resolver ──
    if user_orm is None:
        return {
            "error": (
                "Не удалось получить ORM-объект пользователя для разрешения контакта."
            )
        }

    try:
        from src.core.contacts.contact_resolver import resolve

        candidates = await resolve(
            client,
            user_orm,
            contact_name,
            limit=1,
            min_score=55,
        )
    except Exception as exc:
        logger.exception("Ошибка разрешения контакта %r", contact_name)
        return {"error": f"Ошибка поиска контакта {contact_name!r}: {exc}"}

    if not candidates:
        return {
            "error": (
                f"Контакт {contact_name!r} не найден. "
                "Проверь имя или попроси пользователя уточнить."
            )
        }

    contact = candidates[0]
    chat_id = contact.peer_id

    # ── Получить input_peer для чата ──
    try:
        input_peer = await client.get_input_entity(chat_id)
    except Exception as exc:
        logger.exception("Не удалось получить entity для chat_id=%d", chat_id)
        return {"error": f"Не удалось получить чат контакта {contact_name!r}: {exc}"}

    # ── Найти сообщение по позиции ──
    try:
        position_lower = position.strip().lower()

        if position_lower == "last":
            # Последнее сообщение в чате
            result = await client.get_messages(input_peer, limit=1)
            msgs: list = list(result) if result else []  # type: ignore[arg-type]
            if not msgs:
                return {"error": (f"В чате с {contact.display_name} нет сообщений.")}
            msg = msgs[0]

        elif position_lower == "first":
            # Первое сообщение за сегодня
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            result = await client.get_messages(
                input_peer,
                offset_date=today_start,
                reverse=True,
                limit=1,
            )
            msgs = list(result) if result else []  # type: ignore[arg-type]
            if not msgs:
                # Если за сегодня нет — берём самое последнее
                result = await client.get_messages(input_peer, limit=1)
                msgs = list(result) if result else []  # type: ignore[arg-type]
                if not msgs:
                    return {
                        "error": (f"В чате с {contact.display_name} нет сообщений.")
                    }
            msg = msgs[0]

        else:
            # Числовая позиция: 1 = последнее, 2 = предпоследнее, ...
            try:
                n = int(position_lower)
            except (ValueError, TypeError):
                return {
                    "error": (
                        f"Неверная позиция {position!r}. "
                        "Допустимо: 'last', 'first', или число."
                    )
                }
            if n < 1:
                return {"error": "Позиция должна быть >= 1"}

            result = await client.get_messages(input_peer, limit=n)
            msgs = list(result) if result else []  # type: ignore[arg-type]
            if len(msgs) < n:
                return {
                    "error": (
                        f"В чате с {contact.display_name} только "
                        f"{len(msgs)} сообщ., запрошена позиция {n}."
                    )
                }
            # msgs[0] = самое новое, msgs[-1] = n-е от нового
            msg = msgs[-1]

        # ── Форматировать дату ──
        msg_date = msg.date.isoformat() if msg.date else "неизвестно"
        msg_text = (
            msg.text[:500]
            if msg.text
            else f"[не текст: {type(msg.media).__name__ if msg.media else 'нет контента'}]"
        )

        logger.info(
            "Найдено сообщение от %s (pos=%s): chat_id=%d msg_id=%d",
            contact.display_name,
            position,
            chat_id,
            msg.id,
        )

        return {
            "ok": True,
            "chat_id": chat_id,
            "message_id": msg.id,
            "text": msg_text,
            "date": msg_date,
            "contact": contact.display_name,
        }

    except Exception as exc:
        logger.exception("Ошибка поиска сообщений для контакта %r", contact_name)
        return {"error": f"Ошибка получения сообщений: {exc}"}
