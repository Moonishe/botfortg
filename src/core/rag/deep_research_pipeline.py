"""Конвейер глубокого исследования (Deep Research Pipeline).

Двухфазный процесс:
- Фаза 1 — сбор веб-источников с параллельной загрузкой (semaphore=3),
  генерация подзапросов и сохранение результатов в файлы.
- Фаза 2 — синтез / анализ собранных данных (зарезервировано, заглушка).

Результаты сохраняются в `data_dir/research/<job_id>/`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, UTC
from collections.abc import Callable, Awaitable
from typing import Any

from src.config import settings
from src.core.rag.types import (
    ResearchRequest,
    ResearchResult,
    ResearchSource,
    ResearchStatus,
    ResearchTopic,
)
from src.core.events.event_bus import RESEARCH_COMPLETED, event_bus

logger = logging.getLogger(__name__)

# Максимальное количество параллельных запросов к вебу
_MAX_PARALLEL_FETCHES = 3
# Максимальное количество подзапросов на тему
_MAX_SUB_QUERIES = 5
_MIN_SUB_QUERIES = 3
# Таймаут одного fetch-запроса (сек)
_FETCH_TIMEOUT_SEC = 30.0

# Callback для оповещения о прогрессе исследования
ProgressCallback = Callable[[str, str, str], Awaitable[None]]


class DeepResearchPipeline:
    """Оркестратор двухфазного глубокого исследования.

    Синглтон — один экземпляр на процесс. Хранит задачи в оперативной
    памяти (in-memory dict). Для продакшена нужна персистентность в БД.

    Usage::

        pipeline = get_deep_research_pipeline()
        job_id = await pipeline.submit(ResearchRequest(query="..."))
        result = await pipeline.get_status(job_id)
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ResearchResult] = {}
        self._sem = asyncio.Semaphore(_MAX_PARALLEL_FETCHES)
        self._session: Any = None
        self._user: Any = None
        self._provider: Any = None
        self._progress_callback: ProgressCallback | None = None
        self._pending_tasks: dict[str, asyncio.Task[None]] = {}
        """Фоновые задачи фазы 1 по job_id — для возможности отмены при shutdown."""

    # ── Публичные методы ──────────────────────────────────────────────

    def configure(self, session: Any = None, user: Any = None) -> None:
        """Lazy-init: передать session и user для RAG-модулей.

        Args:
            session: SQLAlchemy AsyncSession (опционально).
            user: ORM User object (опционально).
        """
        self._session = session
        self._user = user

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        """Установить callback для оповещений о прогрессе исследования.

        Callback вызывается с сигнатурой (job_id: str, phase: str, detail: str).
        Установите None чтобы отключить оповещения.

        Args:
            callback: Асинхронная функция оповещения или None.
        """
        self._progress_callback = callback

    async def _notify_progress(self, job_id: str, phase: str, detail: str = "") -> None:
        """Отправить оповещение о прогрессе через callback (если задан).

        Все ошибки внутри callback'а перехватываются — оповещения
        не должны ломать конвейер.
        """
        if self._progress_callback is None:
            return
        try:
            await self._progress_callback(job_id, phase, detail)
        except Exception:
            logger.debug("Progress callback failed (non-critical)", exc_info=True)

    def submit(self, request: ResearchRequest) -> str:
        """Зарегистрировать задачу и запустить фазу 1 в фоне.

        Args:
            request: Параметры исследования (query, max_minutes, фильтры).

        Returns:
            Уникальный job_id (12 hex-символов).

        Задача стартует асинхронно через `asyncio.create_task`.
        Статус сразу становится PENDING, затем PHASE1_RUNNING.
        """
        # Edge guard: validate max_minutes (1–60 range for safety)
        request.max_minutes = max(1, min(60, request.max_minutes))

        # Memory guard: evict oldest jobs if above max
        _MAX_JOBS = 100
        if len(self._jobs) >= _MAX_JOBS:
            oldest = min(
                self._jobs.keys(),
                key=lambda jid: (
                    self._jobs[jid].started_at.timestamp()
                    if self._jobs[jid].started_at
                    else 0
                ),
            )
            logger.info("Evicting oldest research job: %s", oldest)
            del self._jobs[oldest]

        result = ResearchResult(
            query=request.query,
            status=ResearchStatus.PENDING,
            started_at=datetime.now(UTC),
        )
        self._jobs[result.job_id] = result

        # Запуск фазы 1 в фоне — сохраняем ссылку для возможности отмены
        task = asyncio.create_task(
            self._run_phase1(result.job_id, request),
            name=f"deep-research-{result.job_id}",
        )
        self._pending_tasks[result.job_id] = task
        # Автоочистка ссылки при завершении задачи (успех/отмена/ошибка)
        task.add_done_callback(
            lambda _t, jid=result.job_id: self._pending_tasks.pop(jid, None)
        )

        logger.info(
            "Deep research job %s submitted: query=%r max_minutes=%d",
            result.job_id,
            request.query,
            request.max_minutes,
        )
        return result.job_id

    async def get_status(self, job_id: str) -> ResearchResult | None:
        """Получить текущий статус и результат задачи.

        Args:
            job_id: Идентификатор задачи.

        Returns:
            ResearchResult или None, если задача не найдена.
        """
        return self._jobs.get(job_id)

    def get_summary(self, job_id: str) -> str:
        """Get research summary for a job (disk-first, in-memory fallback).

        Checks ``SUMMARY.md`` on disk first; if missing, falls back to
        the in-memory result object.

        Args:
            job_id: Research job identifier.

        Returns:
            Summary Markdown string, or ``""`` if the job is not found.
        """
        # 1. Disk-first — read SUMMARY.md if it exists
        summary_path = settings.data_dir / "research" / job_id / "SUMMARY.md"
        if summary_path.is_file():
            try:
                return summary_path.read_text(encoding="utf-8")
            except Exception:
                logger.debug(
                    "Failed to read SUMMARY.md for job %s", job_id, exc_info=True
                )

        # 2. In-memory fallback
        result = self._jobs.get(job_id)
        if result is not None:
            return self._generate_summary(result)

        logger.debug("get_summary: job %s not found", job_id)
        return ""

    # ── LLM Provider ──────────────────────────────────────────────────

    async def _build_llm_provider(self) -> object | None:
        """Build LLM provider for RAG modules (lazy, cached).

        Returns:
            Provider instance or None if unavailable.
        """
        if self._provider is not None:
            return self._provider
        try:
            from src.core.rag._provider import get_rag_provider

            if self._user is not None:
                self._provider = await get_rag_provider(
                    purpose="background", telegram_id=self._user.telegram_id
                )
            else:
                logger.debug("_build_llm_provider: user not configured")
        except Exception:
            logger.debug("LLM provider unavailable", exc_info=True)
        return self._provider

    # ── Фаза 1: Сбор веб-источников ───────────────────────────────────

    async def _run_phase1(self, job_id: str, request: ResearchRequest) -> None:
        """Выполнить фазу 1: memory-seed → поиск → подзапросы → swarm → KG → timeline.

        Шаги:
        0. Memory-seeded research (Qdrant prior facts, если включено).
        1. Поиск по основному запросу (через duckduckgo_search).
        2. Генерация 3–5 подзапросов + auto-tool selection.
        3. Параллельная загрузка источников (semaphore=3, опционально Swarm).
        4. Knowledge Graph: contradiction detection (если включено).
        5. Timeline extraction (если включено).
        6. Сохранение topics/*.md, sources/*.md, SUMMARY.md.
        7. Эмиссия RESEARCH_COMPLETED.
        """
        result = self._jobs.get(job_id)
        if result is None:
            logger.error("Job %s not found in registry", job_id)
            return

        result.status = ResearchStatus.PHASE1_RUNNING
        logger.info("Phase 1 started for job %s", job_id)
        await self._notify_progress(
            job_id, "searching", f"👀 Ищу: {request.query[:100]}"
        )

        try:
            # ── Шаг 0: Memory-Seeded Research ──
            if settings.deep_research_memory_seed_enabled and self._user is not None:
                await self._notify_progress(
                    job_id, "memory_seed", "🧠 Копаюсь в долгосрочной памяти…"
                )
                try:
                    from src.core.rag.memory_seed import MemorySeeder

                    seeder = MemorySeeder()
                    seed_ctx = await seeder.seed(
                        request.query,
                        self._user.telegram_id,
                    )
                    if seed_ctx.seed_prompt:
                        logger.debug(
                            "Memory-seeded: %d prior facts", len(seed_ctx.prior_facts)
                        )
                        result.seed_context = seed_ctx
                except Exception:
                    logger.debug("Memory-seeding failed (non-critical)", exc_info=True)

            # ── Шаг 1: Поиск по основному запросу ──
            await self._notify_progress(job_id, "searching", "👀 Выполняю поиск…")
            main_sources = await self._fetch_sources(request.query, request.max_minutes)
            await self._notify_progress(
                job_id, "searching", f"👀 Найдено: {len(main_sources)} источников"
            )

            # ── Шаг 2: Генерация подзапросов ──
            sub_queries = self._generate_sub_queries(request.query)
            await self._notify_progress(
                job_id,
                "deep_dive",
                f"💡 Придумал {len(sub_queries)} уточняющих вопросов",
            )

            # ── Auto-Tool Selection for sub-queries ──
            if settings.deep_research_auto_tools_enabled:
                try:
                    from src.core.rag.tool_selector import ToolSelector

                    selector = ToolSelector()
                    for sq in sub_queries:
                        tools = await selector.select_tools([sq], {"query": sq})
                        if tools:
                            logger.debug(
                                "ToolSelector: %d tools for %r", len(tools), sq
                            )
                except Exception:
                    logger.debug("ToolSelector failed (non-critical)", exc_info=True)

            # ── Шаг 3a: Swarm — parallel search (опционально) ──
            swarm_handled_queries: set[str] = set()
            if settings.deep_research_swarm_enabled:
                await self._notify_progress(
                    job_id, "cross_ref", "🤖 Запускаю рой поисковых агентов…"
                )
                try:
                    from src.core.rag.swarm import SwarmOrchestrator
                    from src.core.rag.types import SwarmSubTask

                    swarm = SwarmOrchestrator(max_parallel=settings.swarm_max_parallel)
                    swarm_queries = sub_queries[: settings.swarm_max_parallel]
                    subtasks = [
                        SwarmSubTask(subtopic=f"sub_{i}", query=sq, priority=i)
                        for i, sq in enumerate(swarm_queries)
                    ]
                    swarm_results, _consensus = await swarm.execute(
                        subtasks,
                        search_fn=lambda q: self._fetch_sources(q, request.max_minutes),
                    )
                    for sr in swarm_results:
                        if sr.status == "completed":
                            result.claims.extend(sr.claims)
                    # Track handled queries to avoid double-fetch in Step 3b
                    swarm_handled_queries = set(swarm_queries)
                    logger.debug("Swarm: %d results", len(swarm_results))
                except Exception:
                    logger.debug(
                        "Swarm failed (non-critical), using linear search",
                        exc_info=True,
                    )

            # ── Шаг 3b: линейный поиск по оставшимся подзапросам ──
            remaining_queries = [
                sq for sq in sub_queries if sq not in swarm_handled_queries
            ]
            if remaining_queries:
                await self._notify_progress(
                    job_id,
                    "deep_dive",
                    f"📥 Скачиваю источники по {len(remaining_queries)} запросам…",
                )
            topic = ResearchTopic(
                topic=request.query,
                sub_queries=sub_queries,
                sources=list(main_sources),
            )

            # Параллельный fetch по оставшимся подзапросам
            if remaining_queries:
                sub_tasks = [
                    self._fetch_sources(sq, request.max_minutes)
                    for sq in remaining_queries
                ]
                sub_results = await asyncio.gather(*sub_tasks, return_exceptions=True)

                for sq, sr in zip(remaining_queries, sub_results, strict=True):
                    if isinstance(sr, BaseException):
                        logger.warning("Sub-query %r failed: %s", sq, sr)
                        continue
                    sub_topic = ResearchTopic(
                        topic=sq,
                        sub_queries=[],
                        sources=sr,  # type: ignore[arg-type]
                    )
                    result.topics.append(sub_topic)

            # Основная тема — последней (для SUMMARY)
            result.topics.insert(0, topic)

            # ── Плоский список всех источников (дедупликация по URL) ──
            seen_urls: set[str] = set()
            all_sources: list[ResearchSource] = []
            for t in result.topics:
                for s in t.sources:
                    if s.url not in seen_urls:
                        seen_urls.add(s.url)
                        all_sources.append(s)
            result.sources = all_sources

            await self._notify_progress(
                job_id,
                "deep_dive",
                f"📊 Собрал: {len(result.topics)} тем, "
                f"{len(result.sources)} источников",
            )

            # ── Knowledge Graph: contradiction detection ──
            if settings.deep_research_kg_enabled and result.claims:
                await self._notify_progress(job_id, "cross_ref", "🕸️ Строю граф знаний…")
                try:
                    provider = await self._build_llm_provider()
                    if provider is not None:
                        from src.core.rag.knowledge_graph import KnowledgeGraph

                        kg = KnowledgeGraph()
                        contradictions = await kg.detect_contradictions(
                            result.claims, provider
                        )
                        result.edges = contradictions
                        logger.debug(
                            "KG: %d claims, %d contradictions",
                            len(result.claims),
                            len(contradictions),
                        )
                except Exception:
                    logger.debug("KnowledgeGraph failed (non-critical)", exc_info=True)

            # ── Timeline Extraction ──
            if settings.deep_research_timeline_enabled and result.claims:
                await self._notify_progress(
                    job_id, "timeline", "⏱️ Строю хронологию событий…"
                )
                try:
                    provider = await self._build_llm_provider()
                    if provider is not None:
                        from src.core.rag.timeline import TimelineExtractor

                        extractor = TimelineExtractor()
                        result.timeline = await extractor.extract(
                            result.claims, provider
                        )
                        timeline_events = (
                            getattr(result.timeline, "events", [])
                            if result.timeline
                            else []
                        )
                        logger.debug(
                            "Timeline: %d events extracted",
                            len(timeline_events),
                        )
                except Exception:
                    logger.debug(
                        "Timeline extraction failed (non-critical)", exc_info=True
                    )

            # ── Шаг 4: Сохранение на диск ──
            await self._notify_progress(job_id, "synthesis", "💾 Сохраняю результаты…")
            await self._save_results(job_id, result)

            # ── Генерация сводки ──
            result.summary = self._generate_summary(result)

            # ── Шаг 5: Завершение ──
            result.status = ResearchStatus.COMPLETED
            result.completed_at = datetime.now(UTC)

            # Эмиссия события
            await event_bus.emit(
                RESEARCH_COMPLETED,
                job_id=job_id,
                result=result,
            )

            await self._notify_progress(
                job_id,
                "completed",
                (
                    f"🎉 Исследование завершено!\n"
                    f"• Тем: {len(result.topics)}\n"
                    f"• Источников: {len(result.sources)}\n"
                    f"• Результаты: data/research/{job_id}/"
                ),
            )

            logger.info(
                "Phase 1 completed for job %s: %d topics, %d sources",
                job_id,
                len(result.topics),
                len(result.sources),
            )

        except Exception as exc:
            logger.exception("Phase 1 failed for job %s", job_id)
            result.status = ResearchStatus.FAILED
            result.error = str(exc)
            result.completed_at = datetime.now(UTC)
            await self._notify_progress(
                job_id, "failed", f"💥 Ошибка: {str(exc)[:200]}"
            )

    # ── Fetch источников ──────────────────────────────────────────────

    async def _fetch_sources(
        self, query: str, max_minutes: int
    ) -> list[ResearchSource]:
        """Загрузить источники по поисковому запросу.

        Использует DuckDuckGo (duckduckgo-search) для поиска.
        Jina API для полного текста — заглушка (TODO 2026-06-13).

        Args:
            query: Поисковый запрос.
            max_minutes: Лимит времени (используется для таймаута).

        Returns:
            Список ResearchSource (может быть пустым при ошибке).
        """
        try:
            await asyncio.wait_for(
                self._sem.acquire(),
                timeout=min(max_minutes * 10, 30.0),
            )
        except TimeoutError:
            logger.warning("Semaphore acquire timeout for query %r", query)
            return []

        try:
            sources = await asyncio.wait_for(
                self._do_fetch(query),
                timeout=min(max_minutes * 20, _FETCH_TIMEOUT_SEC),
            )
            return sources
        except TimeoutError:
            logger.warning("Fetch timeout for query %r", query)
            return []
        except Exception:
            logger.exception("Fetch failed for query %r", query)
            return []
        finally:
            self._sem.release()

    async def _do_fetch(self, query: str) -> list[ResearchSource]:
        """Непосредственно выполнить поиск через DuckDuckGo.

        Jina API для загрузки полного текста страницы — заглушка.
        TODO(2026-06-13): интегрировать Jina Reader API для полного контента.
        """
        sources: list[ResearchSource] = []

        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning("duckduckgo-search not installed — returning empty results")
            return sources

        def _sync_search() -> list[dict[str, Any]]:
            ddgs = DDGS()
            try:
                return list(ddgs.text(query, max_results=5))
            finally:
                # DDGS.close() существует в рантайме, но отсутствует в stubs
                _close = getattr(ddgs, "close", None)
                if _close is not None:
                    try:
                        _close()
                    except Exception:
                        logger.debug("Non-critical error", exc_info=True)

        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(_sync_search), timeout=15.0
            )
        except TimeoutError:
            logger.warning("DDG search timeout for query %r", query)
            return sources

        now = datetime.now(UTC)
        for r in results:
            url = r.get("href", "")
            if not url:
                continue

            source = ResearchSource(
                url=url,
                title=r.get("title", ""),
                snippet=r.get("body", ""),
                content="",  # TODO(2026-06-13): Jina API stub
                relevance_score=0.5,  # базовая оценка, без ML
                retrieved_at=now,
            )
            sources.append(source)

        logger.debug("Fetched %d sources for query %r", len(sources), query)
        return sources

    # ── Генерация подзапросов ─────────────────────────────────────────

    def _generate_sub_queries(self, query: str) -> list[str]:
        """Разбить исходный запрос на 3–5 уточняющих подзапросов.

        Реализация: шаблонная генерация без LLM (чтобы не зависеть
        от доступности модели). Добавляет модификаторы к исходному
        запросу: «примеры», «статистика», «мнения», «история», «прогноз».

        TODO(2026-06-13): в будущем — LLM-генерация через lightweight модель.

        Args:
            query: Исходный поисковый запрос.

        Returns:
            Список из 3–5 подзапросов.
        """
        # Edge guard: strip to avoid leading spaces in sub-queries
        query = query.strip()
        if not query:
            return []
        modifiers = [
            f"{query} примеры использование",
            f"{query} статистика данные 2024 2025",
            f"{query} мнения экспертов обзоры",
            f"{query} история развитие эволюция",
            f"{query} прогноз будущее тренды",
        ]

        # Детерминированно выбираем количество на основе длины запроса
        qlen = len(query)
        if qlen < 30:
            count = _MAX_SUB_QUERIES  # короткий запрос → больше подзапросов
        elif qlen < 80:
            count = 4
        else:
            count = _MIN_SUB_QUERIES  # длинный запрос → меньше подзапросов

        return modifiers[:count]

    # ── Сохранение результатов ────────────────────────────────────────

    async def _save_results(self, job_id: str, result: ResearchResult) -> None:
        """Сохранить результаты на диск в data_dir/research/<job_id>/.

        Структура:
            topics/<topic_index>_<safe_name>.md  — каждая тема
            sources/<source_index>.md            — каждый источник
            SUMMARY.md                           — сводка
        """
        base_dir = settings.data_dir / "research" / job_id
        topics_dir = base_dir / "topics"
        sources_dir = base_dir / "sources"

        # Создание директорий в executor'е (чтобы не блокировать event-loop)
        loop = asyncio.get_running_loop()

        def _mkdirs() -> None:
            os.makedirs(str(topics_dir), exist_ok=True)
            os.makedirs(str(sources_dir), exist_ok=True)

        await loop.run_in_executor(None, _mkdirs)

        # Сохраняем темы
        for idx, topic in enumerate(result.topics):
            topic_md = self._format_topic_md(topic, idx)
            topic_path = topics_dir / f"{idx:02d}_{self._safe_filename(topic.topic)}.md"
            await loop.run_in_executor(
                None, lambda p=topic_path, c=topic_md: p.write_text(c, encoding="utf-8")
            )

        # Сохраняем источники
        for idx, source in enumerate(result.sources):
            source_md = self._format_source_md(source, idx)
            source_path = sources_dir / f"{idx:03d}.md"
            await loop.run_in_executor(
                None,
                lambda p=source_path, c=source_md: p.write_text(c, encoding="utf-8"),
            )

        # Сохраняем SUMMARY.md
        summary_md = self._generate_summary(result)
        summary_path = base_dir / "SUMMARY.md"
        await loop.run_in_executor(
            None,
            lambda p=summary_path, c=summary_md: p.write_text(c, encoding="utf-8"),
        )

        logger.info("Results saved to %s", base_dir)

    @staticmethod
    def _safe_filename(name: str) -> str:
        """Преобразовать строку в безопасное имя файла."""
        safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name)
        return safe[:80].strip().replace(" ", "_") or "untitled"

    # ── Форматирование ────────────────────────────────────────────────

    @staticmethod
    def _format_topic_md(topic: ResearchTopic, idx: int) -> str:
        """Форматировать тему в GFM Markdown (совместимо с Rich Messages)."""
        lines = [
            f"# Тема {idx + 1}: {topic.topic}",
            "",
            f"**Подзапросы:** {', '.join(topic.sub_queries) if topic.sub_queries else '—'}",
            "",
            f"**Источников:** {len(topic.sources)}",
            "",
            "## Источники",
            "",
        ]
        if topic.sources:
            lines.extend(
                [
                    "| # | Название | Сниппет |",
                    "|---|---------|--------|",
                ]
            )
            for si, src in enumerate(topic.sources, 1):
                title = (
                    (src.title or src.url)[:100]
                    .replace("|", "\\|")
                    .replace("[", "\\[")
                    .replace("]", "\\]")
                )
                # URL: encode ) to %29 to avoid breaking Markdown link syntax
                safe_url = src.url.replace(")", "%29")
                snippet = (
                    (src.snippet or "—")[:150]
                    .replace("|", "\\|")
                    .replace("\n", " ")
                    .replace("\r", " ")
                    .replace("\t", " ")
                )
                lines.append(f"| {si} | [{title}]({safe_url}) | {snippet} |")
            lines.append("")
        else:
            lines.extend(["*Нет источников*", ""])
        return "\n".join(lines)

    @staticmethod
    def _format_source_md(source: ResearchSource, idx: int) -> str:
        """Форматировать источник в Markdown."""
        lines = [
            f"# Источник {idx + 1}",
            "",
            f"- **URL:** {source.url}",
            f"- **Заголовок:** {source.title or '—'}",
            f"- **Релевантность:** {source.relevance_score:.2f}",
            f"- **Получен:** {source.retrieved_at.isoformat() if source.retrieved_at else '—'}",
            "",
            "## Сниппет",
            "",
            source.snippet or "—",
        ]
        if source.content:
            lines.extend(["", "## Полный текст", "", source.content[:5000]])
        return "\n".join(lines)

    @staticmethod
    def _truncate_md(text: str, max_chars: int = 32000) -> str:
        """Обрезать Markdown до безопасного размера для Telegram Rich Messages.

        Пытается обрезать по границе параграфа. Если не удаётся —
        жёсткая обрезка с добавлением маркера обрезания.
        """
        if len(text) <= max_chars:
            return text

        # Пытаемся найти ближайший двойной перенос строки (конец параграфа)
        cut = text.rfind("\n\n", 0, max_chars - 100)
        if cut < max_chars // 2:
            cut = max_chars - 100
        return text[:cut] + "\n\n… *(обрезано — полный отчёт в файле)*"

    def _generate_summary(self, result: ResearchResult) -> str:
        """Сгенерировать сводку исследования в GFM Markdown (совместимо с Rich Messages)."""
        lines = [
            f"# Сводка исследования: {result.query.replace(chr(10), ' ').strip()}",
            "",
            f"- **Job ID:** `{result.job_id}`",
            f"- **Статус:** {result.status.value}",
            f"- **Начат:** {result.started_at.isoformat() if result.started_at else '—'}",
            f"- **Завершён:** {result.completed_at.isoformat() if result.completed_at else '—'}",
            f"- **Тем:** {len(result.topics)}",
            f"- **Источников:** {len(result.sources)}",
        ]
        if result.error:
            lines.extend(["", f"**Ошибка:** {result.error}"])

        # ── Темы: GFM-таблица ──
        if result.topics:
            lines.extend(
                [
                    "",
                    "## Темы",
                    "",
                    "| # | Тема | Подзапросов | Источников |",
                    "|---|------|------------|-----------|",
                ]
            )
            for i, topic in enumerate(result.topics, 1):
                src_count = len(topic.sources)
                # Экранируем pipe в названии темы и обрезаем длинные названия
                raw_topic = topic.topic.replace("|", "\\|").replace("\n", " ")
                if len(raw_topic) > 80:
                    safe_topic = raw_topic[:77] + "..."
                else:
                    safe_topic = raw_topic
                lines.append(
                    f"| {i} | **{safe_topic}** | {len(topic.sub_queries)} | {src_count} |"
                )

        # ── Все источники: GFM-таблица ──
        if result.sources:
            lines.extend(
                [
                    "",
                    "## Все источники",
                    "",
                    "| # | Название | URL |",
                    "|---|---------|-----|",
                ]
            )
            for i, src in enumerate(result.sources, 1):
                title = (src.title or src.url)[:120].replace("|", "\\|")
                safe_url = src.url.replace("|", "\\|")
                lines.append(f"| {i} | {title} | {safe_url} |")

        # ── Timeline section (сворачиваемый блок) ──
        timeline = getattr(result, "timeline", None)
        if timeline is not None and hasattr(timeline, "events") and timeline.events:
            try:
                from src.core.rag.timeline import TimelineExtractor

                extractor = TimelineExtractor()
                timeline_md = extractor.export_markdown(timeline)
                lines.extend(
                    [
                        "",
                        "<details>",
                        "<summary>**📅 Хронология** (нажмите чтобы развернуть)</summary>",
                        "",
                        timeline_md,
                        "",
                        "</details>",
                    ]
                )
            except Exception:
                logger.debug(
                    "Timeline export_markdown failed (non-critical)", exc_info=True
                )

        return self._truncate_md("\n".join(lines))


# ── Синглтон ──────────────────────────────────────────────────────────

_pipeline: DeepResearchPipeline | None = None


def get_deep_research_pipeline() -> DeepResearchPipeline:
    """Получить глобальный экземпляр DeepResearchPipeline (синглтон)."""
    global _pipeline
    if _pipeline is None:
        _pipeline = DeepResearchPipeline()
    return _pipeline
