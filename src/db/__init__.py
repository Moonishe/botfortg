"""Database package — re-exports session, repo helpers and key models."""

from src.db.models import Contact, Memory, Message, User
from src.db.repo import (
    get_contact,
    get_or_create_user,
    get_self_profile,
    list_contacts,
    search_memories,
    upsert_message,
    upsert_self_profile,
)
from src.db.session import get_session

__all__ = [
    "Contact",
    "Memory",
    "Message",
    "User",
    "get_contact",
    "get_or_create_user",
    "get_self_profile",
    "get_session",
    "list_contacts",
    "search_memories",
    "upsert_message",
    "upsert_self_profile",
]
