"""Contextual inline keyboards for post-action messages."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def smart_post_action_keyboard(
    action_type: str,
    extra_data: dict | None = None,
) -> InlineKeyboardMarkup:
    """Создаёт контекстную клавиатуру после выполнения действия.

    Args:
        action_type: Тип действия — 'send', 'edit', 'search', 'info', 'general'
        extra_data: Доп. данные (например {'peer_id': ..., 'action_id': ..., 'query': ...})
    """
    extra = extra_data or {}
    peer_id = extra.get("peer_id", "")

    if action_type == "send":
        buttons = [
            [
                InlineKeyboardButton(
                    text="✏️ Написать ещё",
                    callback_data=f"send:again:{peer_id}",
                ),
                InlineKeyboardButton(
                    text="💬 Спросить",
                    callback_data="nav:chat",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data="nav:help",
                ),
            ],
        ]
    elif action_type == "edit":
        buttons = [
            [
                InlineKeyboardButton(
                    text="📋 Отправить",
                    callback_data=f"send:again:{peer_id}",
                ),
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data="nav:help",
                ),
            ],
        ]
    elif action_type == "search":
        buttons = [
            [
                InlineKeyboardButton(
                    text="🔍 Уточнить",
                    callback_data="nav:chat",
                ),
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data="nav:help",
                ),
            ],
        ]
    elif action_type in ("cancel",):
        buttons = [
            [
                InlineKeyboardButton(
                    text="💬 Спросить",
                    callback_data="nav:chat",
                ),
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data="nav:help",
                ),
            ],
        ]
    else:  # general / info / fallback
        buttons = [
            [
                InlineKeyboardButton(
                    text="💬 Спросить",
                    callback_data="nav:chat",
                ),
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data="nav:help",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Помощь",
                    callback_data="nav:help",
                ),
            ],
        ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
