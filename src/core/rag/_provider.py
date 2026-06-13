"""Shared LLM provider factory for RAG modules.

Eliminates duplicated session/user/provider-building logic
across ingest, memory_seed, and deep_research_pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)


async def get_rag_provider(
    purpose: str = "rag",
    telegram_id: int | None = None,
) -> Any:
    """Get an LLM provider for RAG tasks. Returns ``None`` if unavailable.

    Creates a fresh session, resolves the user, and builds the provider
    in one call.  Uses ``settings.owner_telegram_id`` when *telegram_id*
    is not explicitly provided.

    Args:
        purpose: Provider purpose (default ``"rag"``).  Controls API-key
            slot selection and model routing.  Use ``"background"`` for
            non-interactive / batch tasks.
        telegram_id: Target Telegram user ID.  Defaults to owner.

    Returns:
        An ``LLMProvider``-compatible object or ``None``.
    """
    from src.db.session import get_session
    from src.db.repo import get_or_create_user
    from src.llm.router import build_provider

    uid = telegram_id if telegram_id is not None else settings.owner_telegram_id
    try:
        async with get_session() as session:
            user = await get_or_create_user(session, uid)
            return await build_provider(session, user, purpose=purpose)
    except Exception:
        logger.debug("RAG provider unavailable", exc_info=True)
        return None
