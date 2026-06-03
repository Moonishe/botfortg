"""Shared router for settings.

SRP: only the router + owner filter. All handlers/modules import from here.
This breaks the circular import chain that used to flow through settings.py.
"""

import logging

from aiogram import Router

from src.bot.filters import OwnerOnly

logger = logging.getLogger(__name__)

router = Router(name="settings")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())
