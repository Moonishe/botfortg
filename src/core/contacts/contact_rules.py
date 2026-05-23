"""Per-contact style rules — stored in ContactProfile.custom_instructions JSON."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def get_contact_rules_block(
    owner_telegram_id: int, contact_id: int
) -> str | None:
    """Get contact-specific rules formatted as a prompt block.

    Args:
        owner_telegram_id: Telegram ID владельца.
        contact_id: peer_id контакта.

    Returns:
        Отформатированный блок правил или None если правил нет.
    """
    from src.db.repo import get_contact_profile, get_or_create_user
    from src.db.session import get_session

    async with get_session() as session:
        user = await get_or_create_user(session, owner_telegram_id)
        profile = await get_contact_profile(session, user, contact_id)
        if not profile or not profile.custom_instructions:
            return None

        try:
            data = json.loads(profile.custom_instructions)
            rules = data.get("rules", [])
            if not rules:
                return None
            lines = ["[ПРАВИЛА ДЛЯ ЭТОГО КОНТАКТА]"]
            for r in rules[-5:]:  # последние 5 правил
                lines.append(f"- {r}")
            return "\n".join(lines)
        except (json.JSONDecodeError, TypeError):
            return None


async def add_contact_rule(owner_telegram_id: int, contact_id: int, rule: str) -> bool:
    """Add a rule for a specific contact.

    Args:
        owner_telegram_id: Telegram ID владельца.
        contact_id: peer_id контакта.
        rule: текст правила.

    Returns:
        True если сохранено, False если контакт не найден.
    """
    from src.db.repo import get_contact_profile, get_or_create_user
    from src.db.session import get_session

    async with get_session() as session:
        user = await get_or_create_user(session, owner_telegram_id)
        profile = await get_contact_profile(session, user, contact_id)
        if not profile:
            return False

        try:
            data = json.loads(profile.custom_instructions or "{}")
        except json.JSONDecodeError:
            data = {}

        rules = data.get("rules", [])
        # Dedup
        if rule not in rules:
            rules.append(rule)
            # Keep max 10 rules per contact
            if len(rules) > 10:
                rules = rules[-10:]
            data["rules"] = rules
            profile.custom_instructions = json.dumps(data, ensure_ascii=False)
            await session.flush()
        return True
