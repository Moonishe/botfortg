"""Factory functions for AppContainer dependencies."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import Settings
from src.core.infra.transcription import TranscriptionService

logger = logging.getLogger(__name__)


# ── Settings ─────────────────────────────────────────────────────────


def provide_settings() -> Settings:
    """Load settings from config / .env (returns the existing singleton)."""
    from src.config import settings

    return settings


# ── Database ─────────────────────────────────────────────────────────


def provide_db_session_factory(settings: Settings) -> async_sessionmaker:
    """Create an async SQLAlchemy session factory.

    Re-uses the global ``SessionLocal`` from ``src.db.session`` when
    available (avoids double-engine creation).  Accepts *settings* for
    future customisation.
    """
    from src.db.session import SessionLocal

    return SessionLocal


# ── LLM router ───────────────────────────────────────────────────────


async def provide_llm_router(
    settings: Settings,
    session_factory: async_sessionmaker,
) -> None:
    """Initialise global LLM router state (locks, circuit breakers).

    The actual ``ProviderFallback`` instances are built per-user by
    ``build_provider(session, user)`` — this function only ensures the
    global async locks are created inside a running event loop.
    """
    from src.llm.router import ensure_locks_initialized

    await ensure_locks_initialized()
    logger.info("LLM router global locks initialized")
    # Intentionally returns None — ``build_provider`` already serves as
    # the router factory and must not be duplicated here.


# ── Transcription ────────────────────────────────────────────────────


def provide_transcription_service(settings: Settings) -> TranscriptionService:
    """Create a TranscriptionService instance."""
    return TranscriptionService(model_size="small")
