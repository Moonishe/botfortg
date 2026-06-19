import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from src.bot import start_voice_worker, stop_voice_worker
from src.bot.app import run_bot, run_bot_webhook
from src.core.infra.app_context import get_app_context
from src.core.memory.memory_queue import start_worker, stop_worker
from src.core.infra.task_manager import task_manager, stop_ff_tasks, track_ff
from src.core.infra.update_notifier import check_and_notify_update
from src.core.memory.auto_save_batch import get_batch_buffer
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
_CLEANUP_INTERVAL = 300  # секунд — интервал между тяжёлыми очистками состояния
_CACHE_CLEANUP_INTERVAL = 60.0  # секунд — интервал фоновой очистки TTL кэша
_PREFETCH_TOP_N = 5  # элементов — количество контактов для предзагрузки
_PREFETCH_RECALL_TOP_N = 3  # элементов — количество recall-паттернов для предзагрузки
_PREFETCH_RECALL_LIMIT = 10  # фактов — лимит recall при cold-start прогреве


logger = logging.getLogger(__name__)


class _JsonlFileHandler(logging.handlers.RotatingFileHandler):
    """JSONL sink: one JSON object per line, redacted via KeyMaskFilter.
    Module-level so it can be used from main() regardless of LOG_FORMAT.
    """

    def __init__(self, filename, *, maxBytes=50_000_000, backupCount=5):
        super().__init__(
            filename, maxBytes=maxBytes, backupCount=backupCount, encoding="utf-8"
        )
        from src.core.infra.key_guard import KeyMaskFilter

        self.addFilter(KeyMaskFilter())
        self.setFormatter(
            logging.Formatter("%(message)s")
        )  # raw json string, no prefix


# ponytail: QueueHandler+QueueListener — non-blocking JSONL, one daemon thread per sink.
# emit() = put_nowait (O(1), microseconds). Listener thread does sync file I/O off event loop.
_jsonl_listeners: list[logging.handlers.QueueListener] = []


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
    from src.bot.handlers.free_text import register_cleanup_timer

    register_cleanup_timer()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    # NOTE: Для агрегации логов в production — установите LOG_FORMAT=json
    # в .env. Тогда логи будут выводиться в JSON-формате (ключи: timestamp,
    # level, logger, message, module, lineno).
    if os.getenv("LOG_FORMAT", "").lower() == "json":
        _setup_json_logging()

    # KeyMaskFilter: маскирует API-ключи даже в plain-text логах
    from src.core.infra.key_guard import KeyMaskFilter

    for _handler in logging.root.handlers:
        _handler.addFilter(KeyMaskFilter())

        # JSONL sinks (llm.jsonl, perf.jsonl, audit.log) — gated by settings.reward_loop_enabled
        #
        # Architecture: QueueHandler (producer, event loop) + QueueListener (consumer, daemon thread).
        # Producer: logger.info(json_str) → QueueHandler.emit → q.put_nowait(record) → returns
        #   immediately (microseconds). No blocking of the asyncio event loop.
        # Consumer: QueueListener daemon thread reads queue → RotatingFileHandler.emit → disk write.
        #   All sync I/O happens off the event loop.
        #
        # Bounded queue (maxsize=10000): prevents unbounded memory growth under burst load.
        # On overflow, records are dropped with a stderr warning (best-effort observability).
        if settings.reward_loop_enabled:
            from queue import Queue, Full
            from logging.handlers import QueueHandler, QueueListener

            class _BoundedQueueHandler(QueueHandler):
                """QueueHandler that drops records on overflow instead of blocking."""

                def emit(self, record):
                    try:
                        self.queue.put_nowait(record)
                    except Full:
                        pass  # drop on overflow (best-effort observability)

            log_dir = settings.data_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            for name, filename in [
                ("llm.jsonl", "llm.jsonl"),
                ("perf.jsonl", "perf.jsonl"),
                ("audit.log", "audit.log"),
            ]:
                lg = logging.getLogger(name)
                lg.propagate = False  # don't double-log to stderr
                lg.setLevel(logging.INFO)
                file_handler = _JsonlFileHandler(str(log_dir / filename))
                q: Queue = Queue(maxsize=10000)
                qh = _BoundedQueueHandler(q)
                ql = QueueListener(q, file_handler, respect_handler_level=True)
                ql.start()
                _jsonl_listeners.append(ql)
                lg.addHandler(qh)

    logger.info("=== main() START ===")
    logger.info("Starting TelegramAssistant")

    # Ensure InaccessibleMessage monkeypatch is applied before any handler runs
    import src.bot.callback_utils  # noqa: F401 — side effect: patches InaccessibleMessage

    # --- Обработчики сигналов для graceful shutdown ---
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    if main_task is None:
        raise RuntimeError("main() must be called via asyncio.run()")

    def _shutdown() -> None:
        logger.info("Received shutdown signal, cancelling main task...")
        main_task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except (NotImplementedError, ValueError):
            # Windows: add_signal_handler не поддерживается — fallback на signal.signal.
            # signal.signal из non-main thread или для SIGTERM на Windows выбрасывает
            # ValueError; пропускаем такие случаи.
            try:
                signal.signal(sig, lambda s, f: main_task.cancel())
            except (ValueError, RuntimeError):
                logger.warning(
                    "Signal handler for %s not registered (platform limitation)",
                    sig.name,
                )

    # Initialize all shutdown-tracked tasks before any await so that CancelledError
    # during startup never causes NameError in the finally block.
    _cleanup_task: asyncio.Task | None = None
    _update_check_task: asyncio.Task | None = None
    _snapshot_task: asyncio.Task | None = None
    _pending_cleanup_task: asyncio.Task | None = None
    _health_check_task: asyncio.Task | None = None
    _dek_rotation_task: asyncio.Task | None = None
    _prefetch_task: asyncio.Task | None = None

    await init_db()

    # --- Snapshot restore: загружаем in-memory состояние с диска ---
    snapshot_engine = None  # prevent NameError on import failure
    try:
        from src.core.state.snapshot_engine import snapshot_engine as _se

        snapshot_engine = _se
        restored = await _se.restore()
        if restored:
            logger.info("Snapshot restored from disk")
    except Exception:
        logger.debug("Snapshot restore skipped (non-critical)", exc_info=True)

    # --- Humanizer: загружаем фидбек из БД после инициализации схемы ---
    try:
        from src.core.humanizer.humanizer import load_humanizer_feedback

        await load_humanizer_feedback()
    except Exception:
        logger.debug(
            "Non-critical error", exc_info=True
        )  # non-critical — humanizer работает и без БД-фидбека

    # --- Issue 1 fix: wire CircuitTelemetry to EventBus (zero-import) ---
    try:
        from src.core.observability.circuit_telemetry import circuit_telemetry
        from src.core.actions.tool_middleware import ToolCircuitBreaker

        await circuit_telemetry.init()
        await ToolCircuitBreaker._push_full_state()
        logger.debug("CircuitTelemetry → EventBus wiring complete")
    except Exception:
        logger.debug("CircuitTelemetry wiring failed (non-critical)", exc_info=True)

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

    from src.core.memory.context_files import (
        index_contexts_to_fts,
        init_owner_context,
        set_main_loop,
    )

    init_owner_context()
    set_main_loop(asyncio.get_running_loop())
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
        logger.debug("hooks.emit('on_startup') failed (non-critical)", exc_info=True)

    await start_worker()
    start_voice_worker()
    notification_queue.start()

    from src.core.cache.manager import cache_manager

    # Start proactive TTL cleanup for all registered ManagedCache instances
    await cache_manager.start_background_cleanup(interval=_CACHE_CLEANUP_INTERVAL)

    async def _cleanup_global_state() -> None:
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL)
            # --- Circuit breaker cleanup (LLM provider keys) ---
            try:
                from src.llm.router import cleanup_circuit_breakers

                removed = await cleanup_circuit_breakers()
                if removed:
                    logger.info(
                        "Circuit breaker cleanup: removed %d stale entries",
                        removed,
                    )
            except asyncio.CancelledError:
                raise  # propagate for clean shutdown
            except Exception:
                logger.debug("circuit_breaker cleanup failed", exc_info=True)
            # --- Tool-level circuit breaker cleanup ---
            try:
                from src.core.actions.tool_middleware import ToolCircuitBreaker

                removed_tcb = await ToolCircuitBreaker.cleanup_stale()
                if removed_tcb:
                    logger.info(
                        "ToolCircuitBreaker cleanup: removed %d idle entries",
                        removed_tcb,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("ToolCircuitBreaker cleanup failed", exc_info=True)
            # --- ToolCircuitBreaker telemetry state refresh ---
            try:
                await ToolCircuitBreaker._push_full_state()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("ToolCircuitBreaker push_full_state failed", exc_info=True)
            # --- Decision-repair guard cleanup ---
            try:
                from src.core.actions.tool_middleware import DecisionRepairGuard

                removed_drg = DecisionRepairGuard.cleanup_stale()
                if removed_drg:
                    logger.info(
                        "DecisionRepairGuard cleanup: evicted %d stale entries",
                        removed_drg,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("DecisionRepairGuard cleanup failed", exc_info=True)
            # --- Circuit telemetry cleanup ---
            try:
                from src.core.observability.circuit_telemetry import circuit_telemetry

                removed_ct = await circuit_telemetry.cleanup_stale()
                if removed_ct:
                    logger.info(
                        "CircuitTelemetry cleanup: removed %d empty entries",
                        removed_ct,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("CircuitTelemetry cleanup failed", exc_info=True)
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
            except asyncio.CancelledError:
                raise
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
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("working_memory cleanup failed", exc_info=True)

    _cleanup_task = track_ff(asyncio.create_task(_cleanup_global_state()))
    _update_check_task = asyncio.create_task(check_and_notify_update())

    from src.core.actions.vector_store import get_vector_store

    await (await get_vector_store()).check_health_and_recover()

    userbot_manager = UserbotManager()
    await userbot_manager.restore_all()

    async def _run_periodic(
        coro_factory: Callable[[], Awaitable[Any]], interval: int
    ) -> None:
        """Run a coroutine periodically every ``interval`` seconds."""
        while True:
            await asyncio.sleep(interval)
            try:
                await coro_factory()
            except Exception:
                logger.debug("Periodic task failed", exc_info=True)

    # --- Background: snapshot save (every 5 minutes) ---
    if snapshot_engine is not None:
        _snapshot_task = track_ff(
            asyncio.create_task(
                _run_periodic(snapshot_engine.save_to_disk, interval=300)
            )
        )

    # --- Background: pending login TTL cleanup (every 5 minutes) ---
    _pending_cleanup_task = track_ff(
        asyncio.create_task(
            _run_periodic(userbot_manager.cleanup_stale_pending, interval=300)
        )
    )

    # --- Background: userbot health check (every 5 minutes) ---
    _health_check_task = asyncio.create_task(userbot_manager.health_check_loop())

    # --- Key Rotation: инициализация KEK/DEK менеджера ---
    _dek_rotation_task: asyncio.Task | None = None
    if settings.key_rotation_enabled:
        try:
            from src.core.crypto.key_rotation import init_rotation_manager

            _kek = settings.encryption_key.encode()
            _mgr = init_rotation_manager(_kek)
            from src.db.session import get_session

            logger.info("KeyRotationManager инициализирован (KEK/DEK)")

            # Auto-rotate DEK every 30 days (background)
            async def _auto_rotate_dek() -> None:
                while True:
                    await asyncio.sleep(86400 * 30)
                    try:
                        async with get_session() as _rot_sess:
                            await _mgr.load_from_db(_rot_sess)
                            await _mgr.rotate()
                            await _rot_sess.commit()
                            logger.info("DEK rotation completed successfully")
                    except Exception:
                        logger.exception("DEK auto-rotation failed")

            _dek_rotation_task = asyncio.create_task(_auto_rotate_dek())
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
                        logger.debug("Recall warmup failed", exc_info=True)

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
        _shutdown_tasks = [
            (_t, _name)
            for _t, _name in [
                (_cleanup_task, "cleanup"),
                (_update_check_task, "update_check"),
                (_prefetch_task, "startup_prefetch"),
                (_snapshot_task, "snapshot"),
                (_pending_cleanup_task, "pending_cleanup"),
                (_health_check_task, "health_check"),
            ]
            if _t is not None
        ]
        if _dek_rotation_task:
            _shutdown_tasks.append((_dek_rotation_task, "dek_rotation"))
        for _t, _name in _shutdown_tasks:
            _t.cancel()
            try:
                await asyncio.wait_for(_t, timeout=_SHUTDOWN_TASK_TIMEOUT)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                logger.exception("%s task cancellation failed", _name)

        async def _shutdown_avito_rotator() -> None:
            from src.core.avito.service import shutdown_avito_rotator

            await shutdown_avito_rotator()

        # Save final snapshot before shutting down components
        if snapshot_engine is not None:
            try:
                await asyncio.wait_for(snapshot_engine.save_to_disk(), timeout=10.0)
                logger.info("Final snapshot saved")
            except TimeoutError:
                logger.warning("Snapshot save timed out during shutdown")
            except Exception:
                logger.debug("Snapshot save failed during shutdown", exc_info=True)

        for step, coro in [
            ("userbot", userbot_manager.shutdown()),
            ("background tasks", task_manager.stop_all()),
            ("memory worker", stop_worker()),
            ("voice worker", stop_voice_worker()),
            ("notification queue", notification_queue.stop()),
            ("cache manager", cache_manager.stop_background_cleanup()),
            ("avito rotator", _shutdown_avito_rotator()),
        ]:
            try:
                logger.debug("Stopping %s…", step)
                await asyncio.wait_for(coro, timeout=_SHUTDOWN_STEP_TIMEOUT)
            except TimeoutError:
                logger.warning("%s shutdown timed out — forcing", step)
            except Exception:
                logger.exception("%s shutdown failed", step)

        # Flush any pending auto-save batch before waiting for fire-and-forget
        # tasks. Without this, buffered facts may be lost during shutdown.
        try:
            buffer = await get_batch_buffer()
            await asyncio.wait_for(buffer.flush_now(), timeout=5.0)
            logger.info("Auto-save buffer flushed")
        except TimeoutError:
            logger.warning("Auto-save buffer flush timed out during shutdown")
        except Exception:
            logger.debug("Auto-save buffer flush failed during shutdown", exc_info=True)

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
            logger.debug(
                "hooks.emit('on_shutdown') failed (non-critical)", exc_info=True
            )

        logger.info("Shutdown complete")

        # Stop JSONL QueueListeners BEFORE logging.shutdown.
        #
        # ql.stop() in stdlib does: enqueue_sentinel() → _thread.join() → handler.close().
        # The join waits for the daemon thread to drain the queue and finish disk writes.
        # RotatingFileHandler (50MB, 5 backups) can take several seconds on slow disk.
        #
        # If we time out, the listener thread may still be writing, and logging.shutdown()
        # will close its RotatingFileHandler from underneath — this is a known race for
        # forced shutdowns, but the daemon thread dies on process exit anyway.
        _ql_stop_timeout = 10.0  # generous — disk I/O for 50MB rotation can be slow
        for ql in _jsonl_listeners:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(ql.stop),
                    timeout=_ql_stop_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "QueueListener stop timed out (%.1fs) — forced shutdown; "
                    "daemon thread will exit with process",
                    _ql_stop_timeout,
                )
            except Exception:
                logger.warning(
                    "QueueListener stop failed — forced shutdown",
                    exc_info=True,
                )
        _jsonl_listeners.clear()

        # Flush + close all remaining logging handlers (stderr + any unclosed JSONL sinks).
        # logging.shutdown() iterates logging._handlerList (weakrefs to ALL handlers),
        # calling flush() + close() — safe to call after QueueListener handlers are
        # already closed by ql.stop() (close() is idempotent on file handlers).
        logging.shutdown()


async def _close_resource(
    name: str,
    close_fn: Callable[[], Awaitable[Any]],
    *,
    timeout: float = 5.0,
) -> None:
    """Run a close coroutine with timeout and uniform logging.

    Failures are swallowed — shutdown must continue regardless of any single
    resource misbehaving.
    """
    try:
        await asyncio.wait_for(close_fn(), timeout=timeout)
        logger.debug("%s closed", name)
    except TimeoutError:
        logger.warning("%s close timed out", name)
    except Exception:
        logger.debug("%s close failed (non-critical)", name, exc_info=True)


async def _close_shared_resources() -> None:
    """Close globally-shared persistent connections on shutdown.

    These are module-level singletons that maintain long-lived connections
    (httpx.AsyncClient, aiosqlite.Connection, sqlite3.Connection). Without
    explicit close, file descriptors and WAL/SHM sidecar files leak until
    the interpreter exits.

    Each resource is closed via :func:`_close_resource`, which enforces timeout
    and error handling.  New resources are added as a single line below.
    """
    # Lazy imports avoid dragging heavy modules in at import time.
    from src.core.actions.embedding_cache import close as _ec_close
    from src.core.actions.mcp_oauth import mcp_oauth
    from src.core.actions.mcp_timer import cancel_all_timers
    from src.core.actions.pubmed_client import close_client
    from src.core.avito.service import _close_stealth_session
    from src.core.intelligence.dsm import close_dsm_db
    from src.core.memory.context_files import close_qdrant
    from src.bot.handlers.avito_cmd import close_avito_cache_db
    from src.db.session import engine

    await _close_resource("embedding_cache", _ec_close, timeout=5.0)
    await _close_resource("mcp_timer tasks", cancel_all_timers, timeout=3.0)
    await _close_resource("pubmed HTTP client", close_client, timeout=5.0)
    await _close_resource("mcp_oauth HTTP client", mcp_oauth.close, timeout=5.0)
    await _close_resource("avito stealth session", _close_stealth_session, timeout=10.0)
    await _close_resource("dsm database connection", close_dsm_db, timeout=5.0)
    await _close_resource(
        "avito query-cache database connection", close_avito_cache_db, timeout=5.0
    )
    await _close_resource(
        "qdrant client", lambda: asyncio.to_thread(close_qdrant), timeout=5.0
    )
    await _close_resource("main SQLAlchemy engine", engine.dispose, timeout=5.0)


def run() -> None:
    logger.info("=== run() START ===")

    # Ensure data directory exists before Alembic tries to open the DB.
    # Without this, a fresh install fails because init_db() (which creates
    # the directory via settings.data_dir property) runs after Alembic.
    # Accessing the property triggers mkdir(parents=True, exist_ok=True).
    _data_dir = settings.data_dir

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

    # На каждый retry создаётся отдельный ThreadPoolExecutor чтобы
    # гарантировать чистый старт. При таймауте shutdown(wait=False)
    # не ждёт зависший поток, но Alembic идемпотентен — повторный
    # upgrade head на частично мигрированной БД либо завершит
    # миграцию, либо безопасно пропустит уже применённые шаги.
    # SQLite WAL + busy_timeout=30s защищают от гонки между старым
    # и новым потоком на уровне БД.

    for _attempt in range(1, _MIGRATION_MAX_RETRIES + 1):
        logger.info(
            "=== alembic upgrade head (attempt %d/%d, timeout=%ds, head=%s) ===",
            _attempt,
            _MIGRATION_MAX_RETRIES,
            _MIGRATION_TIMEOUT,
            head_rev,
        )

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(alembic.command.upgrade, _cfg, "head")
            future.result(timeout=_MIGRATION_TIMEOUT)
            logger.info("=== alembic DONE, entering asyncio ===")
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
            # Не ждём зависшие future — при таймауте миграция ещё выполняется
            # в фоновом потоке и shutdown(wait=True) заблокирует retry loop.
            # Используем wait=False, cancel_futures=True для немедленного
            # освобождения ресурсов без блокировки.
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
            _bg_task = _loop.create_task(main())
            _bg_task.add_done_callback(
                lambda t: (
                    logger.exception(
                        "main() background task failed unexpectedly",
                        exc_info=exc,
                    )
                    if (exc := t.exception()) is not None
                    and not isinstance(exc, asyncio.CancelledError)
                    else None
                )
            )
        except Exception:
            logger.critical(
                "Failed to schedule main() in existing event loop",
                exc_info=True,
            )
            raise SystemExit(1) from None


if __name__ == "__main__":
    run()
