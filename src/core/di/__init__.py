"""Simple Dependency Injection container for TelegramHelper."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import Settings
from src.core.infra.transcription import TranscriptionService
from src.llm.router import ProviderFallback

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Type alias kept for backward-compatibility with callers that expect
# "LLMRouter" — in this codebase the router role is played by
# ``ProviderFallback`` (returned by ``build_provider()``).
LLMRouter = ProviderFallback


@dataclass
class AppContainer:
    """Root DI container.

    Usage::

        container = AppContainer()
        await container.initialize()
        try:
            await run_bot(container)
        finally:
            await container.shutdown()
    """

    settings: Settings | None = field(default=None)
    db_session_factory: async_sessionmaker | None = field(default=None)
    llm_router: LLMRouter | None = field(default=None)
    transcription_service: TranscriptionService | None = field(default=None)

    _initialized: bool = field(default=False, repr=False)

    # ── Lifecycle ────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create and wire all dependencies.

        Raises ``RuntimeError`` on any critical failure so the caller
        can abort startup cleanly.
        """
        if self._initialized:
            logger.warning("AppContainer.initialize() called twice — skipped")
            return

        # 1. Settings
        from src.core.di.providers import provide_settings

        try:
            self.settings = provide_settings()
        except Exception as exc:
            raise RuntimeError(f"Failed to load settings: {exc}") from exc

        # 2. DB session factory
        from src.core.di.providers import provide_db_session_factory

        try:
            self.db_session_factory = provide_db_session_factory(self.settings)
        except Exception as exc:
            raise RuntimeError(f"Failed to create DB session factory: {exc}") from exc

        # 3. Transcription service
        from src.core.di.providers import provide_transcription_service

        try:
            self.transcription_service = provide_transcription_service(self.settings)
        except Exception as exc:
            raise RuntimeError(f"Failed to create TranscriptionService: {exc}") from exc

        # 4. LLM router (provider fallback) — note: the actual per-user
        #    ``ProviderFallback`` instances are built lazily via
        #    ``build_provider(session, user)`` which already lives in
        #    ``src.llm.router``.  Here we only ensure the global locks
        #    are initialised so the first call succeeds.
        from src.core.di.providers import provide_llm_router

        try:
            self.llm_router = await provide_llm_router(
                self.settings, self.db_session_factory
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize LLM router: {exc}") from exc

        self._initialized = True
        logger.info("AppContainer initialized successfully")

    async def shutdown(self) -> None:
        """Release resources held by the container."""
        if not self._initialized:
            return

        errors: list[Exception] = []

        # Dispose the engine behind the session factory
        if self.db_session_factory is not None:
            try:
                await self.db_session_factory.configure(bind=None).dispose()  # type: ignore[attr-defined]
            except AttributeError:
                # async_sessionmaker has no .dispose(); dispose via engine
                try:
                    from src.db.session import engine

                    await engine.dispose()
                except Exception as exc:
                    errors.append(exc)
                    logger.exception("Error disposing DB engine")

        self._initialized = False
        logger.info("AppContainer shut down")

        if errors:
            logger.warning("Shutdown completed with %d error(s)", len(errors))
