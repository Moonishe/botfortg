import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from src.bot.app import run_bot
from src.bot.handlers.free_text import start_voice_worker, stop_voice_worker
from src.core.memory.memory_queue import start_worker, stop_worker
from src.core.infra.task_manager import task_manager, stop_ff_tasks
from src.core.infra.update_notifier import check_and_notify_update
from src.config import PROJECT_ROOT, settings
from src.db.session import init_db
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)


def _register_background_tasks() -> None:
    """Импортирует модули с background-задачами — декораторы авторегистрируют их."""
    # noqa — импорты триггерят @task_manager.task() декораторы
    import src.core.infra.system_tasks  # noqa: F401
    import src.core.scheduling.digest  # noqa: F401
    import src.core.scheduling.reminders  # noqa: F401
    import src.core.scheduling.news  # noqa: F401
    import src.core.infra.auto_sync  # noqa: F401
    import src.core.memory.memory_checker  # noqa: F401
    import src.core.memory.memory_consolidator  # noqa: F401
    import src.core.scheduling.smart_digest  # noqa: F401
    import src.core.scheduling.proactive_briefing  # noqa: F401
    import src.core.scheduling.follow_up  # noqa: F401
    import src.core.scheduling.sleep_tracker  # noqa: F401
    import src.core.scheduling.weekly_summarizer  # noqa: F401
    import src.core.scheduling.weekly_digest  # noqa: F401
    import src.core.memory.memory_patterns  # noqa: F401
    import src.core.memory.knowledge_distiller  # noqa: F401
    import src.core.memory.temporal_layers  # noqa: F401
    import src.core.actions.conflict_resolver  # noqa: F401
    import src.core.actions.conflict_predictor  # noqa: F401
    import src.core.scheduling.habit_tracker  # noqa: F401
    import src.core.memory.memory_clusterer  # noqa: F401
    import src.core.intelligence.skills  # noqa: F401
    import src.core.intelligence.skills_curator  # noqa: F401
    import src.core.intelligence.auto_evolve  # noqa: F401
    import src.core.intelligence.burnout_detector  # noqa: F401
    import src.core.scheduling.dream_cycle  # noqa: F401
    import src.core.scheduling.proactive_nudge  # noqa: F401
    import src.core.scheduling.avito  # noqa: F401
    import src.core.scheduling.message_scheduler  # noqa: F401
    import src.core.rag.ingest  # noqa: F401 — rag_watchdog


async def main() -> None:
    import sys

    sys.stderr.write("=== main() START ===\n")
    sys.stderr.flush()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    logger.info("Starting TelegramAssistant")

    await init_db()

    # --- LLM router: initialize global locks (safe: inside event loop) ---
    from src.llm.router import ensure_locks_initialized

    await ensure_locks_initialized()

    # --- Cold-start skill seeding ---
    if settings.skill_seed_on_startup:
        from sqlalchemy import select as _s
        from src.db.session import get_session
        from src.db.models import User
        from src.core.intelligence.skill_seeder import seed_skills_from_docs

        async with get_session() as _session:
            _r = await _session.execute(
                _s(User.id).where(User.telegram_id == settings.owner_telegram_id)
            )
            _owner_id = _r.scalar_one_or_none()
            if _owner_id is not None:
                _count = await seed_skills_from_docs(_session, user_id=_owner_id)
                logger.info("Seeded %d skills from SKILL.md files", _count)
            else:
                logger.warning(
                    "Owner user (telegram_id=%d) not found — skipping skill seed",
                    settings.owner_telegram_id,
                )

    # --- Gating: check runtime dependencies ---
    from src.core.infra.gating import gates
    from src.core.infra.gating_checks import register_default_gates

    register_default_gates()
    gates.run_all()

    from src.core.scheduling.notification_queue import notification_queue

    # Notify owner about missing dependencies with install hints
    _missing = gates.missing_install_hints
    if _missing:
        _msgs = ["⚠️ **Не хватает зависимостей:**\n"]
        for m in _missing:
            _msgs.append(f"• {m['description']}: `{m['install_hint']}`")
        _msgs.append("\nПроверь `/gates` для полной картины.")

        await notification_queue.enqueue(
            topic="system",
            text="\n".join(_msgs),
            priority=1,
        )

    # --- Context Engine: register pluggable providers ---
    from src.core.context.engine import engine
    from src.core.context.providers.memory_provider import MemoryProvider
    from src.core.context.providers.vector_provider import VectorProvider
    from src.core.context.providers.wiki_context_provider import WikiContextProvider
    from src.core.context.providers.frozen_provider import frozen_provider
    from src.core.context.providers.subdirectory_hints import subdirectory_provider

    from src.core.context.providers.document_provider import DocumentProvider

    engine.register(MemoryProvider())
    engine.register(VectorProvider())
    engine.register(WikiContextProvider())
    engine.register(DocumentProvider())
    engine.register(frozen_provider)
    logger.info("Context engine registered %d providers", len(engine.providers))

    # SubdirectoryHintProvider is special — it doesn't go through engine.gather()
    # It's called manually after tool calls via subdirectory_provider.on_tool_args()
    logger.info(
        "SubdirectoryHintProvider loaded (root=%s, visited={%s})",
        subdirectory_provider.root,
        subdirectory_provider.visited,
    )

    from src.core.memory.context_files import index_contexts_to_fts, init_owner_context

    init_owner_context()
    try:
        count = index_contexts_to_fts()
        if count:
            logger.info("FTS5 context index: %d files", count)
    except Exception:
        logger.warning("FTS5 context indexing failed (non-critical)", exc_info=True)

    try:
        from src.core.infra.hooks import hooks

        await hooks.emit("on_startup")
    except Exception:
        pass  # hooks are optional, never break core flow

    await start_worker()
    start_voice_worker()
    notification_queue.start()

    from src.core.cache.manager import cache_manager

    # Start proactive TTL cleanup for all registered ManagedCache instances
    await cache_manager.start_background_cleanup(interval=60.0)

    async def _cleanup_global_state():
        _tick = 0
        while True:
            await asyncio.sleep(60)
            # Cleanup stale circuit breakers every 300s (5 min)
            _tick += 1
            if _tick >= 5:
                _tick = 0
                try:
                    from src.llm.router import cleanup_circuit_breakers

                    removed = await cleanup_circuit_breakers()
                    if removed:
                        logger.info(
                            "Circuit breaker cleanup: removed %d stale entries",
                            removed,
                        )
                except Exception:
                    logger.debug("circuit_breaker cleanup failed", exc_info=True)

    _cleanup_task = asyncio.create_task(_cleanup_global_state())
    _update_check_task = asyncio.create_task(check_and_notify_update())

    from src.core.actions.vector_store import get_vector_store

    await get_vector_store().check_health_and_recover()

    userbot_manager = UserbotManager()
    await userbot_manager.restore_all()

    _register_background_tasks()
    task_manager.start_all()

    # Phase 2: регистрация MCP-инструментов в tool_registry
    from src.core.actions import register_builtin_tools

    register_builtin_tools()

    # --- Predictive Prefetch: warm caches for frequently accessed data ---
    # Runs in background (non-blocking) after DB init + workers are ready.
    # On first start (no history), queries DB for top contacts directly.
    from src.core.cache.prefetch import prefetch_tracker

    async def _startup_prefetch() -> None:
        """Pre-warm caches for top contacts and recent memories."""
        try:
            from sqlalchemy import desc, select

            from src.config import settings
            from src.core.contacts.contact_memory_digest import get_contact_digest
            from src.db.models._contacts import Contact
            from src.db.repo import get_or_create_user
            from src.db.session import get_session

            # Register warmup callback for contact digests
            async def _warmup_contact_digest(peer_id: int) -> None:
                await get_contact_digest(settings.owner_telegram_id, int(peer_id))

            prefetch_tracker.register_warmup("contact_digest", _warmup_contact_digest)

            # Check if we have prior access history
            top_contacts = prefetch_tracker.get_top_keys("contact_digest", top_n=5)

            if not top_contacts:
                # Cold start: query DB for most active contacts
                async with get_session() as session:
                    owner = await get_or_create_user(
                        session, settings.owner_telegram_id
                    )
                    if owner is not None:
                        result = await session.execute(
                            select(Contact.peer_id)
                            .where(Contact.user_id == owner.id)
                            .order_by(desc(Contact.id))
                            .limit(5)
                        )
                        top_contacts = [row[0] for row in result.fetchall()]
                        logger.info(
                            "Cold-start prefetch: found %d active contacts",
                            len(top_contacts),
                        )

            # Prefetch contact digests (top-5)
            if top_contacts:
                prefetched = await prefetch_tracker.prefetch_predictions(
                    "contact_digest", top_n=5
                )
                logger.info("Startup prefetch: %d contact digests warmed", prefetched)

            # Register warmup callback for recall (uses recent memory queries)
            async def _warmup_recall(cache_key: str) -> None:
                """Lightweight warmup: load recent pinned memories."""
                import asyncio

                from src.core.infra.task_manager import track_ff

                async def _do_warmup() -> None:
                    try:
                        from src.core.memory.memory_recall import (
                            _make_recall_cache_key,
                            _recall_cache,
                            recall,
                        )

                        # Only warm up if not already cached
                        existing = await _recall_cache.get(cache_key)
                        if existing is None:
                            # Run a lightweight recall to warm the cache
                            # We can't easily reconstruct params from cache_key,
                            # so just run a default "recent pinned" query
                            await recall(
                                settings.owner_telegram_id,
                                query="",
                                limit=10,
                                include_self=False,
                                include_pinned=True,
                                include_tasks=False,
                                include_deep=False,
                                mode="light",
                            )
                    except Exception:
                        pass  # warmup best-effort

                track_ff(asyncio.create_task(_do_warmup()))

            prefetch_tracker.register_warmup("recall", _warmup_recall)

            # Prefetch recent memory patterns (top-3)
            recent_recalls = prefetch_tracker.get_top_keys("recall", top_n=3)
            if recent_recalls:
                prefetched = await prefetch_tracker.prefetch_predictions(
                    "recall", top_n=3
                )
                logger.info("Startup prefetch: %d recall patterns warmed", prefetched)
            else:
                # Cold start: trigger one baseline recall to warm the pipeline
                try:
                    from src.core.memory.memory_recall import recall

                    await recall(
                        settings.owner_telegram_id,
                        query="",
                        limit=5,
                        include_self=False,
                        include_pinned=True,
                        include_tasks=False,
                        include_deep=False,
                        mode="light",
                    )
                    logger.info("Cold-start prefetch: baseline recall warmed")
                except Exception:
                    logger.debug("Cold-start recall prefetch failed", exc_info=True)
        except Exception:
            # Prefetch is best-effort — never break startup
            logger.warning("Startup prefetch failed (non-critical)", exc_info=True)

    _prefetch_task = asyncio.create_task(_startup_prefetch())

    try:
        await run_bot(userbot_manager)
    finally:
        logger.info("Shutting down…")

        # Cancel background tasks first
        for _t, _name in [
            (_cleanup_task, "cleanup"),
            (_update_check_task, "update_check"),
            (_prefetch_task, "startup_prefetch"),
        ]:
            _t.cancel()
            try:
                await asyncio.wait_for(_t, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                logger.exception("%s task cancellation failed", _name)

        for step, coro in [
            ("userbot", userbot_manager.shutdown()),
            ("background tasks", task_manager.stop_all()),
            ("memory worker", stop_worker()),
            ("voice worker", stop_voice_worker()),
            ("notification queue", notification_queue.stop()),
            ("cache manager", cache_manager.stop_background_cleanup()),
        ]:
            try:
                logger.debug("Stopping %s…", step)
                await asyncio.wait_for(coro, timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("%s shutdown timed out — forcing", step)
            except Exception:
                logger.exception("%s shutdown failed", step)

        # Give fire-and-forget tasks (fact saves, trajectory, inbox) a chance
        # to finish so in-flight DB writes are not lost.
        try:
            await asyncio.wait_for(stop_ff_tasks(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("fire-and-forget tasks shutdown timed out")
        except Exception:
            logger.exception("fire-and-forget tasks shutdown failed")

        try:
            from src.core.actions.vector_store import get_vector_store

            await asyncio.wait_for(get_vector_store().shutdown(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("vector_store shutdown timed out")
        except Exception:
            logger.exception("vector_store shutdown failed")

        # Close the shared Playwright browser (singleton in mcp_playwright).
        # Without this explicit close, the Chromium process leaks across
        # reloads in dev and only gets killed when the interpreter exits.
        try:
            from src.core.actions.mcp_playwright import _browser_manager

            await asyncio.wait_for(_browser_manager.close(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("playwright browser shutdown timed out")
        except Exception:
            logger.debug(
                "playwright browser shutdown failed (likely never started)",
                exc_info=True,
            )

        try:
            from src.core.infra.hooks import hooks

            await hooks.emit("on_shutdown")
        except Exception:
            pass  # hooks are optional, never break core flow

        logger.info("Shutdown complete")


def run() -> None:
    import sys

    sys.stderr.write("=== run() START ===\n")
    sys.stderr.flush()

    # --- Schema migrations (Alembic — with 30s timeout on Railway) ---
    import alembic.command
    import alembic.config
    from alembic.script import ScriptDirectory

    _cfg = alembic.config.Config(str(PROJECT_ROOT / "alembic.ini"))
    _script = ScriptDirectory.from_config(_cfg)
    head_rev = _script.get_current_head()

    sys.stderr.write(
        f"=== alembic upgrade head START (timeout=120s, head={head_rev}) ===\n"
    )
    sys.stderr.flush()

    executor = ThreadPoolExecutor(max_workers=1)
    alembic_ok = False
    try:
        future = executor.submit(alembic.command.upgrade, _cfg, "head")
        future.result(timeout=120)
        alembic_ok = True
        sys.stderr.write("=== alembic DONE, entering asyncio ===\n")
        sys.stderr.flush()
    except FutureTimeoutError:
        sys.stderr.write(
            "=== alembic TIMEOUT — stamping head and using init_db() fallback ===\n"
        )
        sys.stderr.flush()
        # Alembic hung — stamp the head revision and let init_db() create tables
        import sqlite3

        _db_url = str(PROJECT_ROOT / "data" / "app.db")
        _conn = sqlite3.connect(_db_url)
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        _conn.execute(
            "INSERT OR REPLACE INTO alembic_version (version_num) VALUES (?)",
            (head_rev,),
        )
        _conn.commit()
        _conn.close()
        sys.stderr.write(f"=== alembic stamped head={head_rev} manually ===\n")
        sys.stderr.flush()
    except Exception:
        sys.stderr.write("=== alembic CRASHED ===\n")
        sys.stderr.flush()
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    asyncio.run(main())


if __name__ == "__main__":
    run()
