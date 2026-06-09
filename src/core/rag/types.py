"""Типы данных для конвейера глубокого исследования (Deep Research Pipeline).

Data-классы определяют контракты запроса, результата и статуса исследования.
Все поля имеют аннотации типов для статической проверки и документирования.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class ResearchStatus(str, enum.Enum):
    """Статус задачи глубокого исследования."""

    PENDING = "pending"  # задача создана, ожидает запуска
    PHASE1_RUNNING = "phase1_running"  # фаза 1: сбор веб-источников
    PHASE2_RUNNING = "phase2_running"  # фаза 2: синтез/анализ (зарезервировано)
    COMPLETED = "completed"  # исследование завершено успешно
    FAILED = "failed"  # исследование завершилось с ошибкой


@dataclass
class ResearchSource:
    """Один веб-источник, найденный в ходе исследования.

    Attributes:
        url: URL источника.
        title: Заголовок страницы/статьи.
        snippet: Краткий сниппет (первые N символов).
        content: Полный текст страницы (если загружен).
        relevance_score: Оценка релевантности запросу [0.0, 1.0].
        retrieved_at: Время получения источника (UTC).
    """

    url: str
    title: str = ""
    snippet: str = ""
    content: str = ""
    relevance_score: float = 0.0
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ResearchTopic:
    """Одна тема/подтема исследования с подзапросами и источниками.

    Attributes:
        topic: Название темы (оригинальный запрос или подтема).
        sub_queries: Список уточняющих подзапросов (3–5 шт.).
        sources: Источники, собранные по этой теме.
    """

    topic: str
    sub_queries: list[str] = field(default_factory=list)
    sources: list[ResearchSource] = field(default_factory=list)


@dataclass
class ResearchRequest:
    """Запрос на запуск глубокого исследования.

    Attributes:
        query: Основной поисковый запрос.
        max_minutes: Максимальное время выполнения (по умолчанию 5).
        topic_filter: Фильтр по темам (None — без фильтра).
        sources_filter: Фильтр по доменам источников (None — без фильтра).
        owner_id: ID пользователя-владельца (опционально).
    """

    query: str
    max_minutes: int = 5
    topic_filter: Optional[list[str]] = None
    sources_filter: Optional[list[str]] = None
    owner_id: Optional[int] = None


@dataclass
class ResearchResult:
    """Результат глубокого исследования.

    Attributes:
        job_id: Уникальный идентификатор задачи.
        status: Текущий статус исследования.
        query: Оригинальный запрос.
        topics: Собранные темы с источниками.
        summary: Сводный текст (Markdown).
        sources: Плоский список всех источников.
        started_at: Время старта (UTC).
        completed_at: Время завершения (UTC).
        error: Сообщение об ошибке, если status == FAILED.
    """

    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: ResearchStatus = ResearchStatus.PENDING
    query: str = ""
    topics: list[ResearchTopic] = field(default_factory=list)
    summary: str = ""
    sources: list[ResearchSource] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: str = ""
