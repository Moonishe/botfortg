"""Persona pipeline: auto-rebuild SelfProfile when enough personal facts accumulate.

Modeled after TencentDB-Agent-Memory L3 Persona trigger (triggerEveryN=50),
adapted for TelegramHelper's simpler architecture.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.config import settings
from src.db.repo import count_new_personal_facts_since, get_self_profile

logger = logging.getLogger(__name__)


async def maybe_rebuild_persona(
    session,
    owner,
    *,
    llm_provider: str | None = None,
) -> bool:
    """Check if enough new personal facts accumulated, rebuild if so.

    Returns True if persona was rebuilt, False otherwise.
    """
    trigger_n = settings.persona_trigger_every_n_facts
    if trigger_n <= 0:
        return False  # manual-only mode

    # Get last profile timestamp
    profile = await get_self_profile(session, owner)

    since: datetime | None = None
    if profile is not None and profile.updated_at:
        if isinstance(profile.updated_at, datetime):
            since = profile.updated_at
        else:
            since = None

    # Count new personal facts since last profile build
    count = await count_new_personal_facts_since(session, owner, since)

    if count < trigger_n:
        logger.debug(
            "Persona: %d new personal facts (needs %d), skipping",
            count,
            trigger_n,
        )
        return False

    logger.info(
        "Persona: %d new personal facts >= %d, triggering rebuild",
        count,
        trigger_n,
    )

    try:
        from src.core.contacts.self_profile_builder import build_self_profile

        new_profile = await build_self_profile(
            owner.telegram_id,
            provider=llm_provider,
        )
        if new_profile:
            logger.info("Persona rebuilt successfully")
            return True
        else:
            logger.warning("Persona rebuild returned None (not enough facts?)")
            return False
    except Exception:
        logger.exception("Persona rebuild failed")
        return False


__all__ = [
    "maybe_rebuild_persona",
]
