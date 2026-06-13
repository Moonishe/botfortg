"""Типы данных для конвейера глубокого исследования (Deep Research Pipeline).

Data-классы определяют контракты запроса, результата и статуса исследования.
Все поля имеют аннотации типов для статической проверки и документирования.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any


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
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


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
    topic_filter: list[str] | None = None
    sources_filter: list[str] | None = None
    owner_id: int | None = None


class ClaimEdgeType(str, enum.Enum):
    """Тип связи между утверждениями в Knowledge Graph."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CITES = "cites"
    DERIVES_FROM = "derives_from"


@dataclass
class KnowledgeClaim:
    """Одно утверждение/факт, извлечённое из источников."""

    claim_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    text: str = ""
    source_url: str = ""
    confidence: float = 0.5
    entities: list[str] = field(default_factory=list)
    category: str = "fact"


@dataclass
class ContradictionResult:
    """Результат обнаружения противоречия между двумя утверждениями."""

    claim_a: KnowledgeClaim = field(default_factory=KnowledgeClaim)
    claim_b: KnowledgeClaim = field(default_factory=KnowledgeClaim)
    edge_type: ClaimEdgeType = ClaimEdgeType.CONTRADICTS
    confidence: float = 0.5
    explanation: str = ""


@dataclass
class ToolAction:
    """Действие инструмента, выбранное авто-селектором."""

    tool_name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    reason: str = ""


@dataclass
class SwarmSubTask:
    """Подзадача для параллельного swarm-исследования."""

    subtopic: str = ""
    query: str = ""
    priority: int = 0
    depends_on: list[str] = field(default_factory=list)


@dataclass
class SwarmResult:
    """Результат выполнения одной swarm-подзадачи."""

    subtask_id: str = ""
    status: str = "pending"
    sources: list[Any] = field(default_factory=list)
    claims: list[KnowledgeClaim] = field(default_factory=list)
    summary: str = ""


@dataclass
class ConsensusResult:
    """Итог консенсуса между swarm-подзадачами."""

    agreed_claims: list[KnowledgeClaim] = field(default_factory=list)
    disputed_claims: list[KnowledgeClaim] = field(default_factory=list)
    orphan_claims: list[KnowledgeClaim] = field(default_factory=list)
    contradictions: list[ContradictionResult] = field(default_factory=list)


@dataclass
class ResearchContext:
    """Контекст для memory-seeded исследования."""

    prior_facts: list[dict[str, Any]] = field(default_factory=list)
    related_entities: list[str] = field(default_factory=list)
    deep_insights: list[str] = field(default_factory=list)
    previous_research: list[str] = field(default_factory=list)
    seed_prompt: str = ""


@dataclass
class TemporalAssertion:
    """Утверждение с временной привязкой."""

    claim_id: str = ""
    text: str = ""
    date_str: str = ""
    date_parsed: Any = None
    granularity: str = "day"
    confidence: float = 0.5


@dataclass
class TemporalEvent:
    """Событие на временной шкале."""

    assertion: TemporalAssertion = field(default_factory=TemporalAssertion)
    source_url: str = ""
    event_date: Any = None
    description: str = ""


@dataclass
class TemporalContradiction:
    """Противоречие между двумя временными событиями."""

    event_a: TemporalEvent = field(default_factory=TemporalEvent)
    event_b: TemporalEvent = field(default_factory=TemporalEvent)
    contradiction_type: str = ""
    explanation: str = ""


@dataclass
class Timeline:
    """Полная временная шкала событий."""

    events: list[TemporalEvent] = field(default_factory=list)
    contradictions: list[TemporalContradiction] = field(default_factory=list)
    chrono_map: dict[str, list[str]] = field(default_factory=dict)
    generated_at: Any = None


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
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str = ""
    claims: list[KnowledgeClaim] = field(default_factory=list)
    edges: list[ContradictionResult] = field(default_factory=list)
    timeline: Timeline | None = None
    seed_context: ResearchContext | None = None
