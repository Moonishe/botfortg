"""Userbot manager — singleton access helpers."""

from src.userbot.manager import UserbotManager


def get_userbot_manager():
    """Get the singleton UserbotManager instance, or None if not initialized."""
    from src.userbot.manager import _MANAGER_SINGLETON

    return _MANAGER_SINGLETON


def get_active_telethon_client(telegram_id: int):
    """Get active Telethon client for a user, or None."""
    mgr = get_userbot_manager()
    return mgr.get_client(telegram_id) if mgr else None


__all__ = [
    "UserbotManager",
    "get_active_telethon_client",
    "get_userbot_manager",
]
