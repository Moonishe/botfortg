import asyncio
import json
import logging
import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from src.bot.app import run_bot, run_bot_webhook
from src.bot.handlers.free_text import start_voice_worker, stop_voice_worker
from src.core.infra.app_context import get_app_context
from src.core.memory.memory_queue import start_worker, stop_worker
from src.core.infra.task_manager import task_manager, stop_ff_tasks
from src.core.infra.update_notifier import check_and_notify_update
from src.config import PROJECT_ROOT, settings
from src.db.session import init_db
from src.userbot.manager import UserbotManager
from datetime import UTC

# ── Module constants ─────────────────────────────────────────────────────
_SHUTDOWN_TASK_TIMEOUT = 5.0  # секунд — таймаут отмены одной фоновой задачи
_SHUTDOWN_STEP_TIMEOUT = 15.0  # секунд — таймаут одного шага graceful shutdown
_SHUTDOWN_BROWSER_TIMEOUT = 5.0  # секунд — таймаут закрытия Playwright браузера
_SHUTDOWN_FF_TIMEOUT = 10.0  # секунд — таймаут fire-and-forget задач
_SHUTDOWN_VECTOR_TIMEOUT = 10.0  # секунд — таймаут отключения векторного хранилища
_CLEANUP_INTERVAL = 60  # секунд — интервал цикла очистки состояния
_CLEANUP_TICK_INTERVAL = 5  # тактов — интервал между тяжёлыми очистками (5 * 60 = 300s)
_CACHE_CLEANUP_INTERVAL = 60.0  # секунд — интервал фоновой очистки TTL кэша
_PREFETCH_TOP_N = 5  # элементов — количество контактов для предзагрузки
_PREFETCH_RECALL_TOP_N = 3  # элементов — количество recall-паттернов для предзагрузки
_PREFETCH_RECALL_LIMIT = 10  # фактов — лимит recall при cold-start прогреве


logger = logging.getLogger(__name__)


def _setup_json_logging() -> None:
    """Настраивает JSON-формат для логов (агрегация в ELK/Loki/CloudWatch)."""

    from src.core.infra.key_guard import mask_keys

    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            return json.dumps(
                {
                    "timestamp": self.formatTime(record),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": mask_keys(record.getMessage()),
                    "module": record.module,
                    "lineno": record.lineno,
                },
                ensure_ascii=False,
                default=str,
            )

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _register_background_tasks() -> None:
    """Импортирует модули с background-задачами — декораторы авторегистрируют их."""
    # PERF-018: background timer for stale pending confirmation cleanup
    from src.bot.handlers.free_text_pipeline import register_cleanup_timer

    register_cleanup_timer()


async def main() -> None:
    import sys

    sys.stderr.write("=== main() START ===\n")
    sys.stderr.flush()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    # NOTE: Для агрегации логов в production — установите LOG_FORMAT=json
    # в .env. Тогда логи будут выводиться в JSON-формате (ключи: timestamp,
    # level, logger, message, module, lineno).
    if os.getenv("LOG_FORMAT", "").lower() == "json":
        _setup_json_logging()
    logger.info("Starting TelegramAssistant")

    # --- Обработчики сигналов для graceful shutdown ---
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    assert main_task is not None, "main() must be called via asyncio.run()"

    def _shutdown() -> None:
        logger.info("Received shutdown signal, cancelling main task...")
        main_task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Windows: add_signal_handler не поддерживается — fallback на signal.signal
            signal.signal(sig, lambda s, f: main_task.cancel())

    await init_db()

    # --- Humanizer: загружаем фидбек из БД после инициализации схемы ---
    try:
        from src.core.humanizer.humanizer import load_humanizer_feedback

        await load_humanizer_feedback()
    except Exception:
        logger.debug(
            "Non-critical error", exc_info=True
        )  # non-critical — humanizer работает и без БД-фидбека

    # --- LLM router: initialize global locks (safe: inside event loop) ---
    from src.llm.router import ensure_locks_initialized

    await ensure_locks_initialized()

    # --- DI container: wire up all singletons ---
    ctx = get_app_context()
    await ctx.initialize(settings)

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
        count = await index_contexts_to_fts()
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
    await cache_manager.start_background_cleanup(interval=_CACHE_CLEANUP_INTERVAL)

    async def _cleanup_global_state():
        _tick = 0
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL)
            # Cleanup stale circuit breakers + PendingAction + WorkingMemory
            # every _CLEANUP_TICK_INTERVAL * _CLEANUP_INTERVAL = 300s (5 min).
            _tick = (_tick + 1) % _CLEANUP_TICK_INTERVAL
            if _tick != 0:
                continue
            # --- Circuit breaker cleanup ---
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
            # --- PendingAction cleanup ---
            try:
                from src.db.repo import cleanup_expired_actions
                from src.db.session import get_session

                async with get_session() as _cleanup_sess:
                    removed_pa = await cleanup_expired_actions(_cleanup_sess)
                    if removed_pa:
                        logger.info(
                            "PendingAction cleanup: removed %d expired", removed_pa
                        )
            except Exception:
                logger.debug("pending_action cleanup failed", exc_info=True)
            # --- WorkingMemory cleanup ---
            try:
                from datetime import datetime

                from sqlalchemy import delete

                from src.db.models._memory import WorkingMemory
                from src.db.session import get_session

                async with get_session() as _wm_sess:
                    now = datetime.now(UTC)
                    result = await _wm_sess.execute(
                        delete(WorkingMemory).where(
                            WorkingMemory.expires_at.isnot(None),
                            WorkingMemory.expires_at < now,
                        )
                    )
                    removed_wm = result.rowcount
                    if removed_wm:
                        logger.info(
                            "WorkingMemory cleanup: removed %d expired", removed_wm
                        )
            except Exception:
                logger.debug("working_memory cleanup failed", exc_info=True)

    _cleanup_task = asyncio.create_task(_cleanup_global_state())
    _update_check_task = asyncio.create_task(check_and_notify_update())

    from src.core.actions.vector_store import get_vector_store

    await (await get_vector_store()).check_health_and_recover()

    userbot_manager = UserbotManager()
    await userbot_manager.restore_all()

    async def _run_periodic(coro_factory, interval: int):
        """Run a coroutine periodically every ``interval`` seconds."""
        while True:
            await asyncio.sleep(interval)
            try:
                await coro_factory()
            except Exception:
                logger.debug("Periodic task failed", exc_info=True)

    # --- Background: pending login TTL cleanup (every 5 minutes) ---
    _pending_cleanup_task = asyncio.create_task(
        _run_periodic(userbot_manager.cleanup_stale_pending, interval=300)
    )

    # --- Background: userbot health check (every 5 minutes) ---
    _health_check_task = asyncio.create_task(userbot_manager.health_check_loop())

    # --- Key Rotation: инициализация KEK/DEK менеджера ---
    if settings.key_rotation_enabled:
        try:
            from src.core.crypto.key_rotation import init_rotation_manager

            _kek = settings.encryption_key.encode()
            _mgr = init_rotation_manager(_kek)
            logger.info("KeyRotationManager инициализирован (KEK/DEK)")
        except Exception:
            logger.exception("Ошибка инициализации KeyRotationManager")

    _register_background_tasks()
    await task_manager.start_all()

    # Phase 2: регистрация MCP-инструментов в tool_registry
    from src.core.actions import register_builtin_tools

    await asyncio.to_thread(register_builtin_tools)

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
            top_contacts = prefetch_tracker.get_top_keys(
                "contact_digest", top_n=_PREFETCH_TOP_N
            )

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
                            .limit(_PREFETCH_TOP_N)
                        )
                        top_contacts = [row[0] for row in result.fetchall()]
                        logger.info(
                            "Cold-start prefetch: found %d active contacts",
                            len(top_contacts),
                        )

            # Prefetch contact digests (top-5)
            if top_contacts:
                prefetched = await prefetch_tracker.prefetch_predictions(
                    "contact_digest", top_n=_PREFETCH_TOP_N
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
                                limit=_PREFETCH_RECALL_LIMIT,
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
            recent_recalls = prefetch_tracker.get_top_keys(
                "recall", top_n=_PREFETCH_RECALL_TOP_N
            )
            if recent_recalls:
                prefetched = await prefetch_tracker.prefetch_predictions(
                    "recall", top_n=_PREFETCH_RECALL_TOP_N
                )
                logger.info("Startup prefetch: %d recall patterns warmed", prefetched)
            else:
                # Cold start: trigger one baseline recall to warm the pipeline
                try:
                    from src.core.memory.memory_recall import recall

                    await recall(
                        settings.owner_telegram_id,
                        query="",
                        limit=_PREFETCH_TOP_N,
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
        if settings.webhook_url:
            logger.info("Webhook mode: %s", settings.webhook_url)
            await run_bot_webhook(userbot_manager)
        else:
            await run_bot(userbot_manager)
    except asyncio.CancelledError:
        logger.info("Main task cancelled, shutting down...")
    finally:
        logger.info("Shutting down…")

        # Cancel background tasks first
        for _t, _name in [
            (_cleanup_task, "cleanup"),
            (_update_check_task, "update_check"),
            (_prefetch_task, "startup_prefetch"),
            (_pending_cleanup_task, "pending_cleanup"),
            (_health_check_task, "health_check"),
        ]:
            _t.cancel()
            try:
                await asyncio.wait_for(_t, timeout=_SHUTDOWN_TASK_TIMEOUT)
            except (TimeoutError, asyncio.CancelledError):
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
                await asyncio.wait_for(coro, timeout=_SHUTDOWN_STEP_TIMEOUT)
            except TimeoutError:
                logger.warning("%s shutdown timed out — forcing", step)
            except Exception:
                logger.exception("%s shutdown failed", step)

        # Give fire-and-forget tasks (fact saves, trajectory, inbox) a chance
        # to finish so in-flight DB writes are not lost.
        try:
            await asyncio.wait_for(stop_ff_tasks(), timeout=_SHUTDOWN_FF_TIMEOUT)
        except TimeoutError:
            logger.warning("fire-and-forget tasks shutdown timed out")
        except Exception:
            logger.exception("fire-and-forget tasks shutdown failed")

        try:
            from src.core.actions.vector_store import get_vector_store

            await asyncio.wait_for(
                (await get_vector_store()).shutdown(), timeout=_SHUTDOWN_VECTOR_TIMEOUT
            )
        except TimeoutError:
            logger.warning("vector_store shutdown timed out")
        except Exception:
            logger.exception("vector_store shutdown failed")

        # Close the shared Playwright browser (singleton in mcp_playwright).
        # Without this explicit close, the Chromium process leaks across
        # reloads in dev and only gets killed when the interpreter exits.
        try:
            from src.core.actions.mcp_playwright import _close_browser

            await asyncio.wait_for(_close_browser(), timeout=_SHUTDOWN_BROWSER_TIMEOUT)
        except TimeoutError:
            logger.warning("playwright browser shutdown timed out")
        except Exception:
            logger.debug(
                "playwright browser shutdown failed (likely never started)",
                exc_info=True,
            )

        # ── Close persistent connections (httpx, aiosqlite, sqlite3) ──
        # These are global singletons that must be explicitly closed to avoid
        # resource leaks (file descriptors, connection pools, WAL files).
        await _close_shared_resources()

        try:
            from src.core.infra.hooks import hooks

            await hooks.emit("on_shutdown")
        except Exception:
            pass  # hooks are optional, never break core flow

        logger.info("Shutdown complete")


async def _close_shared_resources() -> None:
    """Close globally-shared persistent connections on shutdown.

    These are module-level singletons that maintain long-lived connections
    (httpx.AsyncClient, aiosqlite.Connection, sqlite3.Connection). Without
    explicit close, file descriptors and WAL/SHM sidecar files leak until
    the interpreter exits.
    """
    # embedding_cache aiosqlite connection
    try:
        from src.core.actions.embedding_cache import close as ec_close

        await asyncio.wait_for(ec_close(), timeout=5.0)
        logger.debug("embedding_cache connection closed")
    except TimeoutError:
        logger.warning("embedding_cache close timed out")
    except Exception:
        logger.debug("embedding_cache close failed (non-critical)", exc_info=True)

    # mcp_timer sqlite3 connection (sync — run in thread)
    try:
        from src.core.actions.mcp_timer import close_timer_db

        await asyncio.wait_for(asyncio.to_thread(close_timer_db), timeout=3.0)
        logger.debug("mcp_timer DB connection closed")
    except TimeoutError:
        logger.warning("mcp_timer DB close timed out")
    except Exception:
        logger.debug("mcp_timer DB close failed (non-critical)", exc_info=True)

    # pubmed httpx client
    try:
        from src.core.actions.pubmed_client import close_client

        await asyncio.wait_for(close_client(), timeout=5.0)
        logger.debug("pubmed HTTP client closed")
    except TimeoutError:
        logger.warning("pubmed client close timed out")
    except Exception:
        logger.debug("pubmed client close failed (non-critical)", exc_info=True)

    # mcp_oauth httpx client
    try:
        from src.core.actions.mcp_oauth import mcp_oauth

        await asyncio.wait_for(mcp_oauth.close(), timeout=5.0)
        logger.debug("mcp_oauth HTTP client closed")
    except TimeoutError:
        logger.warning("mcp_oauth close timed out")
    except Exception:
        logger.debug("mcp_oauth close failed (non-critical)", exc_info=True)


def run() -> None:
    import sys

    sys.stderr.write("=== run() START ===\n")
    sys.stderr.flush()

    # --- Schema migrations (Alembic — with 120s timeout and retry) ---
    import time as _time
    import alembic.command
    import alembic.config
    from alembic.script import ScriptDirectory

    _cfg = alembic.config.Config(str(PROJECT_ROOT / "alembic.ini"))
    _script = ScriptDirectory.from_config(_cfg)
    head_rev = _script.get_current_head()

    _MIGRATION_MAX_RETRIES = 3
    _MIGRATION_TIMEOUT = 120
    _MIGRATION_RETRY_DELAY = 10  # seconds base delay (doubles each retry)

    for _attempt in range(1, _MIGRATION_MAX_RETRIES + 1):
        sys.stderr.write(
            f"=== alembic upgrade head (attempt {_attempt}/{_MIGRATION_MAX_RETRIES}, "
            f"timeout={_MIGRATION_TIMEOUT}s, head={head_rev}) ===\n"
        )
        sys.stderr.flush()

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(alembic.command.upgrade, _cfg, "head")
            future.result(timeout=_MIGRATION_TIMEOUT)
            sys.stderr.write("=== alembic DONE, entering asyncio ===\n")
            sys.stderr.flush()
            break  # success — exit retry loop
        except FutureTimeoutError:
            if _attempt < _MIGRATION_MAX_RETRIES:
                delay = _MIGRATION_RETRY_DELAY * (2 ** (_attempt - 1))
                logger.warning(
                    "Alembic migration timed out (attempt %d/%d). Retrying in %ds…",
                    _attempt,
                    _MIGRATION_MAX_RETRIES,
                    delay,
                )
                _time.sleep(delay)
            else:
                logger.critical(
                    "Alembic migration timed out after %d attempts (%ds each). "
                    "Refusing to start.",
                    _MIGRATION_MAX_RETRIES,
                    _MIGRATION_TIMEOUT,
                )
                raise SystemExit(1) from None
        except Exception as e:
            if _attempt < _MIGRATION_MAX_RETRIES:
                delay = _MIGRATION_RETRY_DELAY * (2 ** (_attempt - 1))
                logger.warning(
                    "Alembic migration failed (attempt %d/%d): %s. Retrying in %ds…",
                    _attempt,
                    _MIGRATION_MAX_RETRIES,
                    e,
                    delay,
                )
                _time.sleep(delay)
            else:
                logger.critical(
                    "Alembic migration failed after %d attempts: %s. "
                    "Refusing to start.",
                    _MIGRATION_MAX_RETRIES,
                    e,
                )
                raise SystemExit(1) from e
        finally:
            executor.shutdown(wait=True, cancel_futures=False)
            executor.shutdown(wait=False, cancel_futures=True)

    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — normal startup path
        asyncio.run(main())
    else:
        # Event loop already running (e.g. called from Jupyter, tests,
        # or another async context).  asyncio.run() would crash here.
        # Schedule main() as a task in the existing loop instead.
        logger.warning(
            "run() called while event loop is already running. "
            "Scheduling main() as a background task."
        )
        try:
            _loop.create_task(main())
        except Exception:
            logger.critical(
                "Failed to schedule main() in existing event loop",
                exc_info=True,
            )
            raise SystemExit(1) from None


if __name__ == "__main__":
    run()
