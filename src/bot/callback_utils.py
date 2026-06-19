"""Safe callback-message helpers — handle Message | InaccessibleMessage | None.

aiogram's ``callback.message`` can be an ``InaccessibleMessage`` when the
original message is too old (>48 h) or was deleted.  ``InaccessibleMessage``
lacks ``edit_text`` / ``text`` / ``answer``, so the common
``if callback.message: await callback.message.edit_text(...)`` guard crashes
with ``AttributeError`` on stale inline keyboards.

This module provides:
1. ``safe_callback_edit`` — explicit helper for safe editing
2. ``patch_inaccessible_message`` — monkeypatch that adds no-op ``edit_text``,
   ``delete``, ``edit_reply_markup`` to ``InaccessibleMessage``, fixing ALL
   ~97 direct ``callback.message.edit_text()`` call sites at once
"""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardMarkup,
    Message,
)

logger = logging.getLogger(__name__)

_patch_applied = False


def patch_inaccessible_message() -> None:
    """Add no-op methods to InaccessibleMessage so stale callbacks don't crash.

    ponytail: monkeypatch instead of fixing 97 call sites individually.
    Upgrade path: migrate all handlers to safe_callback_edit, then remove this.
    """
    global _patch_applied
    if _patch_applied:
        return
    _patch_applied = True

    async def _noop_edit_text(self, *args, **kwargs):  # noqa: ANN001
        logger.warning(
            "edit_text called on InaccessibleMessage (stale callback) — "
            "operation silently ignored"
        )
        return False

    async def _noop_delete(self, *args, **kwargs):  # noqa: ANN001
        logger.warning(
            "delete called on InaccessibleMessage (stale callback) — "
            "operation silently ignored"
        )
        return False

    async def _noop_edit_reply_markup(self, *args, **kwargs):  # noqa: ANN001
        logger.warning(
            "edit_reply_markup called on InaccessibleMessage (stale callback) — "
            "operation silently ignored"
        )
        return False

    async def _noop_answer(self, *args, **kwargs):  # noqa: ANN001
        logger.warning(
            "answer called on InaccessibleMessage (stale callback) — "
            "operation silently ignored"
        )
        return False

    # Guard: don't override real methods if aiogram adds them in future versions
    if not hasattr(InaccessibleMessage, "edit_text"):
        InaccessibleMessage.edit_text = _noop_edit_text  # type: ignore[attr-defined]
    if not hasattr(InaccessibleMessage, "delete"):
        InaccessibleMessage.delete = _noop_delete  # type: ignore[attr-defined]
    if not hasattr(InaccessibleMessage, "edit_reply_markup"):
        InaccessibleMessage.edit_reply_markup = _noop_edit_reply_markup  # type: ignore[attr-defined]
    if not hasattr(InaccessibleMessage, "answer"):
        InaccessibleMessage.answer = _noop_answer  # type: ignore[attr-defined]

    # Data attributes: return None for .text/.html_text/.reply_markup
    # Fixes 6 handler sites that access callback.message.text/html_text/reply_markup
    if not hasattr(InaccessibleMessage, "text"):
        InaccessibleMessage.text = None  # type: ignore[attr-defined]
    if not hasattr(InaccessibleMessage, "html_text"):
        InaccessibleMessage.html_text = None  # type: ignore[attr-defined]
    if not hasattr(InaccessibleMessage, "reply_markup"):
        InaccessibleMessage.reply_markup = None  # type: ignore[attr-defined]


# Apply patch at import time — fixes all 97 call sites without touching handlers
patch_inaccessible_message()


async def safe_callback_edit(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Edit ``callback.message`` if it is a live ``Message``; no-op otherwise.

    Returns ``True`` if the message was edited (or was already identical),
    ``False`` if the message is inaccessible / missing (stale inline button).

    Suppresses ``TelegramBadRequest`` for *message is not modified* (idempotent
    re-render) but re-raises genuine Telegram errors.
    """
    msg = callback.message
    if not isinstance(msg, Message):
        return False
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.warning("safe_callback_edit: edit_text failed: %s", exc)
            raise
    return True
