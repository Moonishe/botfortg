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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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

    # ── Публичные методы ──────────────────────────────────────────────

    def submit(self, request: ResearchRequest) -> str:
        """Зарегистрировать задачу и запустить фазу 1 в фоне.

        Args:
            request: Параметры исследования (query, max_minutes, фильтры).

        Returns:
            Уникальный job_id (12 hex-символов).

        Задача стартует асинхронно через `asyncio.create_task`.
        Статус сразу становится PENDING, затем PHASE1_RUNNING.
        """
        result = ResearchResult(
            query=request.query,
            status=ResearchStatus.PENDING,
            started_at=datetime.now(timezone.utc),
        )
        self._jobs[result.job_id] = result

        # Запуск фазы 1 в фоне
        asyncio.create_task(self._run_phase1(result.job_id, request))

        logger.info(
            "Deep research job %s submitted: query=%r max_minutes=%d",
            result.job_id,
            request.query,
            request.max_minutes,
        )
        return result.job_id

    async def get_status(self, job_id: str) -> Optional[ResearchResult]:
        """Получить текущий статус и результат задачи.

        Args:
            job_id: Идентификатор задачи.

        Returns:
            ResearchResult или None, если задача не найдена.
        """
        return self._jobs.get(job_id)

    # ── Фаза 1: Сбор веб-источников ───────────────────────────────────

    async def _run_phase1(self, job_id: str, request: ResearchRequest) -> None:
        """Выполнить фазу 1: поиск → подзапросы → параллельная загрузка.

        Шаги:
        1. Поиск по основному запросу (через duckduckgo_search).
        2. Генерация 3–5 подзапросов.
        3. Параллельная загрузка источников (semaphore=3).
        4. Сохранение topics/*.md, sources/*.md, SUMMARY.md.
        5. Эмиссия RESEARCH_COMPLETED.
        """
        result = self._jobs.get(job_id)
        if result is None:
            logger.error("Job %s not found in registry", job_id)
            return

        result.status = ResearchStatus.PHASE1_RUNNING
        logger.info("Phase 1 started for job %s", job_id)

        try:
            # ── Шаг 1: Поиск по основному запросу ──
            main_sources = await self._fetch_sources(request.query, request.max_minutes)

            # ── Шаг 2: Генерация подзапросов ──
            sub_queries = self._generate_sub_queries(request.query)

            # ── Шаг 3: Параллельная загрузка по подзапросам ──
            topic = ResearchTopic(
                topic=request.query,
                sub_queries=sub_queries,
                sources=list(main_sources),
            )

            # Параллельный fetch по каждому подзапросу
            sub_tasks = [
                self._fetch_sources(sq, request.max_minutes) for sq in sub_queries
            ]
            sub_results = await asyncio.gather(*sub_tasks, return_exceptions=True)

            for sq, sr in zip(sub_queries, sub_results):
                if isinstance(sr, BaseException):
                    logger.warning("Sub-query %r failed: %s", sq, sr)
                    continue
                # sr гарантированно list[ResearchSource] после проверки isinstance
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

            # ── Шаг 4: Сохранение на диск ──
            await self._save_results(job_id, result)

            # ── Генерация сводки ──
            result.summary = self._generate_summary(result)

            # ── Шаг 5: Завершение ──
            result.status = ResearchStatus.COMPLETED
            result.completed_at = datetime.now(timezone.utc)

            # Эмиссия события
            await event_bus.emit(
                RESEARCH_COMPLETED,
                job_id=job_id,
                result=result,
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
            result.completed_at = datetime.now(timezone.utc)

    # ── Fetch источников ──────────────────────────────────────────────

    async def _fetch_sources(
        self, query: str, max_minutes: int
    ) -> list[ResearchSource]:
        """Загрузить источники по поисковому запросу.

        Использует DuckDuckGo (duckduckgo-search) для поиска.
        Jina API для полного текста — заглушка (TODO).

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
        except asyncio.TimeoutError:
            logger.warning("Semaphore acquire timeout for query %r", query)
            return []

        try:
            sources = await asyncio.wait_for(
                self._do_fetch(query),
                timeout=min(max_minutes * 20, _FETCH_TIMEOUT_SEC),
            )
            return sources
        except asyncio.TimeoutError:
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
        TODO: интегрировать Jina Reader API для полного контента.
        """
        sources: list[ResearchSource] = []

        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning("duckduckgo-search not installed — returning empty results")
            return sources

        def _sync_search() -> list[dict]:
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
                        pass

        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(_sync_search), timeout=15.0
            )
        except asyncio.TimeoutError:
            logger.warning("DDG search timeout for query %r", query)
            return sources

        now = datetime.now(timezone.utc)
        for r in results:
            url = r.get("href", "")
            if not url:
                continue

            source = ResearchSource(
                url=url,
                title=r.get("title", ""),
                snippet=r.get("body", ""),
                content="",  # TODO: Jina API stub
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

        TODO: в будущем — LLM-генерация через lightweight модель.

        Args:
            query: Исходный поисковый запрос.

        Returns:
            Список из 3–5 подзапросов.
        """
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
        """Форматировать тему в Markdown."""
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
        for si, src in enumerate(topic.sources, 1):
            lines.append(f"{si}. [{src.title or src.url}]({src.url})")
            if src.snippet:
                lines.append(f"   > {src.snippet[:200]}")
            lines.append("")
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

    def _generate_summary(self, result: ResearchResult) -> str:
        """Сгенерировать сводку исследования в Markdown."""
        lines = [
            f"# Сводка исследования: {result.query}",
            "",
            f"- **Job ID:** {result.job_id}",
            f"- **Статус:** {result.status.value}",
            f"- **Начат:** {result.started_at.isoformat() if result.started_at else '—'}",
            f"- **Завершён:** {result.completed_at.isoformat() if result.completed_at else '—'}",
            f"- **Тем:** {len(result.topics)}",
            f"- **Источников:** {len(result.sources)}",
        ]
        if result.error:
            lines.extend(["", f"**Ошибка:** {result.error}"])

        if result.topics:
            lines.extend(["", "## Темы", ""])
            for i, topic in enumerate(result.topics, 1):
                src_count = len(topic.sources)
                lines.append(
                    f"{i}. **{topic.topic}** "
                    f"(подзапросов: {len(topic.sub_queries)}, "
                    f"источников: {src_count})"
                )

        if result.sources:
            lines.extend(["", "## Все источники", ""])
            for i, src in enumerate(result.sources, 1):
                lines.append(f"{i}. [{src.title or src.url}]({src.url})")

        return "\n".join(lines)


# ── Синглтон ──────────────────────────────────────────────────────────

_pipeline: Optional[DeepResearchPipeline] = None


def get_deep_research_pipeline() -> DeepResearchPipeline:
    """Получить глобальный экземпляр DeepResearchPipeline (синглтон)."""
    global _pipeline
    if _pipeline is None:
        _pipeline = DeepResearchPipeline()
    return _pipeline
