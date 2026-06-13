"""Unit-тесты для 5 новых RAG-модулей: KnowledgeGraph, Swarm, Timeline,
ToolSelector, MemorySeeder."""

from __future__ import annotations

import json
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.rag.knowledge_graph import KnowledgeGraph
from src.core.rag.memory_seed import MemorySeeder
from src.core.rag.swarm import SwarmOrchestrator
from src.core.rag.timeline import TimelineExtractor
from src.core.rag.tool_selector import ToolSelector
from src.core.rag.types import (
    ClaimEdgeType,
    ContradictionResult,
    KnowledgeClaim,
    ResearchContext,
    ResearchSource,
    SwarmResult,
    SwarmSubTask,
    TemporalAssertion,
    TemporalContradiction,
    TemporalEvent,
    Timeline,
)

# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _make_claim(text: str, source_url: str = "", claim_id: str = "") -> KnowledgeClaim:
    """Фабрика KnowledgeClaim для тестов."""
    return KnowledgeClaim(
        claim_id=claim_id or f"c_{hash(text) & 0xFFFF:04x}",
        text=text,
        source_url=source_url,
        confidence=0.8,
    )


def _make_mock_provider(return_value: dict | str) -> AsyncMock:
    """Создать AsyncMock-провайдера, который возвращает заданный JSON."""
    mock = AsyncMock()
    raw = json.dumps(return_value) if isinstance(return_value, dict) else return_value
    mock.chat = AsyncMock(return_value=raw)
    return mock


# ═══════════════════════════════════════════════════════════════════
# 1. KnowledgeGraph — detect_contradictions
# ═══════════════════════════════════════════════════════════════════


class TestKnowledgeGraph:
    """Тесты KnowledgeGraph.detect_contradictions()."""

    @pytest.mark.asyncio
    async def test_пустые_claims__пустые_противоречия(self) -> None:
        """Пустой список утверждений → пустой список противоречий."""
        kg = KnowledgeGraph()
        result = await kg.detect_contradictions([])
        assert result == []

    @pytest.mark.asyncio
    async def test_один_claim_без_провайдера__пустой_результат(self) -> None:
        """Один claim без провайдера → пусто."""
        kg = KnowledgeGraph()
        claims = [_make_claim("Факт A")]
        result = await kg.detect_contradictions(claims)
        assert result == []

    @pytest.mark.asyncio
    async def test_похожие_claims__противоречия_обнаружены(self) -> None:
        """Два похожих по тексту утверждения с мок-LLM → найдено противоречие."""
        kg = KnowledgeGraph()
        claims = [
            _make_claim("Python лучший язык программирования для data science"),
            _make_claim("Python является языком программирования для data science"),
        ]
        provider = _make_mock_provider(
            {
                "contradictions": [
                    {
                        "claim_a_idx": 0,
                        "claim_b_idx": 1,
                        "confidence": 0.85,
                        "explanation": "Разная степень уверенности в утверждениях",
                    }
                ]
            }
        )

        result = await kg.detect_contradictions(claims, provider)

        assert len(result) == 1
        c = result[0]
        assert isinstance(c, ContradictionResult)
        assert c.edge_type == ClaimEdgeType.CONTRADICTS
        assert c.confidence == 0.85
        assert c.claim_a.text == claims[0].text
        assert c.claim_b.text == claims[1].text

    @pytest.mark.asyncio
    async def test_разные_claims__нет_противоречий(self) -> None:
        """Тексты слишком разные по cosine similarity → фаза 1 отсеивает."""
        kg = KnowledgeGraph()
        claims = [
            _make_claim("Python используется в веб-разработке"),
            _make_claim("котики милые пушистые животные"),
        ]
        # Провайдер не должен вызываться, т.к. similarity < 0.3
        provider = _make_mock_provider({"contradictions": []})

        result = await kg.detect_contradictions(claims, provider)

        assert result == []
        provider.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_возвращает_мусор__без_падения(self) -> None:
        """LLM вернул не-JSON / не-словарь → graceful degradation."""
        kg = KnowledgeGraph()
        claims = [
            _make_claim("утверждение A"),
            _make_claim("утверждение A повтор"),
        ]
        provider = _make_mock_provider("not valid json {{{")

        result = await kg.detect_contradictions(claims, provider)

        assert result == []


# ═══════════════════════════════════════════════════════════════════
# 2. SwarmOrchestrator
# ═══════════════════════════════════════════════════════════════════


class TestSwarmOrchestrator:
    """Тесты SwarmOrchestrator.execute() и merge_sources()."""

    @pytest.mark.asyncio
    async def test_пустые_подзадачи__пустой_результат(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Пустой список подзадач → пустой список результатов и пустой консенсус."""
        monkeypatch.setattr(
            "src.core.rag.swarm.settings.deep_research_swarm_enabled", True
        )
        orch = SwarmOrchestrator(max_parallel=2)
        results, consensus = await orch.execute([])

        assert results == []
        assert consensus.agreed_claims == []
        assert consensus.disputed_claims == []

    @pytest.mark.asyncio
    async def test_выполнение_с_подзадачами__результаты_возвращены(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Переданы подзадачи с search_fn → каждый результат completed."""
        monkeypatch.setattr(
            "src.core.rag.swarm.settings.deep_research_swarm_enabled", True
        )
        orch = SwarmOrchestrator(max_parallel=3)

        subtasks = [
            SwarmSubTask(subtopic="тема 1", query="запрос 1"),
            SwarmSubTask(subtopic="тема 2", query="запрос 2"),
        ]

        async def _fake_search(query: str) -> list[ResearchSource]:
            return [
                ResearchSource(
                    url=f"http://example.com/{hash(query) & 0xFF}", title=query
                )
            ]

        results, consensus = await orch.execute(subtasks, search_fn=_fake_search)

        assert len(results) == 2
        assert all(r.status == "completed" for r in results)
        for r in results:
            assert len(r.sources) == 1

    @pytest.mark.asyncio
    async def test_swarm_отключён__пустой_результат(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Флаг deep_research_swarm_enabled=False → execute возвращает пусто."""
        monkeypatch.setattr(
            "src.core.rag.swarm.settings.deep_research_swarm_enabled", False
        )
        orch = SwarmOrchestrator()

        subtasks = [SwarmSubTask(subtopic="t1", query="q1")]
        results, consensus = await orch.execute(subtasks)

        assert results == []
        assert consensus.agreed_claims == []

    def test_merge_sources_дедупликация(self) -> None:
        """merge_sources объединяет источники и убирает дубликаты по URL."""
        results = [
            SwarmResult(
                subtask_id="r1",
                status="completed",
                sources=[
                    ResearchSource(url="http://a.com", title="A"),
                    ResearchSource(url="http://b.com", title="B"),
                ],
            ),
            SwarmResult(
                subtask_id="r2",
                status="completed",
                sources=[
                    ResearchSource(url="http://b.com", title="B dup"),
                    ResearchSource(url="http://c.com", title="C"),
                ],
            ),
            SwarmResult(
                subtask_id="r3",
                status="completed",
                sources=[],  # пустой список
            ),
        ]

        merged = SwarmOrchestrator.merge_sources(results)

        assert len(merged) == 3
        urls = {getattr(s, "url", str(s)) for s in merged}
        assert urls == {"http://a.com", "http://b.com", "http://c.com"}

    def test_merge_sources_пустой_ввод(self) -> None:
        """Пустой список результатов → пустой список источников."""
        merged = SwarmOrchestrator.merge_sources([])
        assert merged == []


# ═══════════════════════════════════════════════════════════════════
# 3. TimelineExtractor
# ═══════════════════════════════════════════════════════════════════


class TestTimelineExtractor:
    """Тесты TimelineExtractor.extract() и export_markdown()."""

    @pytest.mark.asyncio
    async def test_пустые_claims__пустой_таймлайн(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Пустой список claims → Timeline без событий."""
        monkeypatch.setattr(
            "src.core.rag.timeline.settings.deep_research_timeline_enabled", True
        )
        extractor = TimelineExtractor()
        timeline = await extractor.extract([])
        assert timeline.events == []
        assert timeline.contradictions == []
        assert timeline.chrono_map == {}

    @pytest.mark.asyncio
    async def test_claims_с_датами__события_извлечены(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Claims с датами в тексте → TemporalEvent'ы через regex."""
        monkeypatch.setattr(
            "src.core.rag.timeline.settings.deep_research_timeline_enabled", True
        )
        extractor = TimelineExtractor()
        claims = [
            _make_claim("Релиз Python 3.13 состоялся 2024-10-07"),
            _make_claim("Python 3.12 вышел в 2023-10", source_url="https://py.org"),
            _make_claim("Первая версия Python появилась в 1991 году"),
        ]
        timeline = await extractor.extract(claims)
        assert len(timeline.events) >= 3
        assert timeline.chrono_map != {}

    @pytest.mark.asyncio
    async def test_таймлайн_отключён__пусто(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Флаг deep_research_timeline_enabled=False → возврат пустого Timeline."""
        monkeypatch.setattr(
            "src.core.rag.timeline.settings.deep_research_timeline_enabled", False
        )
        extractor = TimelineExtractor()
        claims = [_make_claim("Событие в 2024-06-15")]
        timeline = await extractor.extract(claims)
        assert timeline.events == []

    def test_export_markdown_с_событиями(self) -> None:
        """export_markdown генерирует Markdown с событиями по годам."""
        extractor = TimelineExtractor()
        timeline = Timeline(
            events=[
                TemporalEvent(
                    assertion=TemporalAssertion(
                        claim_id="c1",
                        text="Релиз v1",
                        date_str="2024-03-15",
                        granularity="day",
                    ),
                    source_url="http://a.com",
                    event_date=datetime(2024, 3, 15),
                    description="Релиз версии 1.0",
                ),
                TemporalEvent(
                    assertion=TemporalAssertion(
                        claim_id="c2",
                        text="Релиз v2",
                        date_str="2025-01-20",
                        granularity="day",
                    ),
                    source_url="http://b.com",
                    event_date=datetime(2025, 1, 20),
                    description="Релиз версии 2.0",
                ),
            ],
            contradictions=[
                TemporalContradiction(
                    event_a=TemporalEvent(
                        assertion=TemporalAssertion(date_str="2024-03-15"),
                        event_date=datetime(2024, 3, 15),
                    ),
                    event_b=TemporalEvent(
                        assertion=TemporalAssertion(date_str="2024-03-15"),
                        event_date=datetime(2024, 3, 15),
                    ),
                    contradiction_type="conflicting_date",
                    explanation="2024-03-15 vs 2024-03-15",
                )
            ],
            generated_at=datetime(2025, 6, 1, tzinfo=UTC),
        )
        md = extractor.export_markdown(timeline)
        assert "## 📅 Хронология" in md
        assert "### 2024" in md
        assert "2024-03-15" in md
        assert "### 2025" in md
        assert "2025-01-20" in md
        assert "### ⚠️ Противоречия" in md

    def test_parse_date_разные_форматы(self) -> None:
        """TimelineExtractor._parse_date разбирает day/month/year форматы."""
        # Полная дата
        dt = TimelineExtractor._parse_date("2024-06-15")
        assert dt == datetime(2024, 6, 15)

        # Год-месяц
        dt = TimelineExtractor._parse_date("2024-06")
        assert dt == datetime(2024, 6, 1)

        # Только год
        dt = TimelineExtractor._parse_date("2024")
        assert dt == datetime(2024, 1, 1)

        # Невалидная строка
        dt = TimelineExtractor._parse_date("вчера")
        assert dt is None

    def test_parse_date_некорректные_значения(self) -> None:
        """Невалидные даты → None без исключения."""
        assert TimelineExtractor._parse_date("2024-13-01") is None  # месяц 13
        assert TimelineExtractor._parse_date("2024-02-30") is None  # 30 февраля
        assert TimelineExtractor._parse_date("hello") is None
        assert TimelineExtractor._parse_date("") is None


# ═══════════════════════════════════════════════════════════════════
# 4. ToolSelector
# ═══════════════════════════════════════════════════════════════════


class TestToolSelector:
    """Тесты ToolSelector.select_tools() и _classify_gap()."""

    @pytest.mark.asyncio
    async def test_пустые_gaps__пустой_список(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Пустой список пробелов → пустой список инструментов."""
        monkeypatch.setattr(
            "src.core.rag.tool_selector.settings.deep_research_auto_tools_enabled", True
        )
        monkeypatch.setattr(
            "src.core.rag.tool_selector.settings.tool_selector_max_tools", 3
        )
        selector = ToolSelector()
        actions = await selector.select_tools([])
        assert actions == []

    @pytest.mark.asyncio
    async def test_gap_tweet__возвращает_x_rss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Пробел 'tweet' → классифицируется как missing_social_media → x_rss."""
        monkeypatch.setattr(
            "src.core.rag.tool_selector.settings.deep_research_auto_tools_enabled", True
        )
        monkeypatch.setattr(
            "src.core.rag.tool_selector.settings.tool_selector_max_tools", 3
        )
        selector = ToolSelector()
        actions = await selector.select_tools(["tweet"])
        tool_names = {a.tool_name for a in actions}
        assert "x_rss" in tool_names
        assert all(a.reason for a in actions)

    @pytest.mark.asyncio
    async def test_автовыбор_отключён__пусто(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Флаг deep_research_auto_tools_enabled=False → пустой результат."""
        monkeypatch.setattr(
            "src.core.rag.tool_selector.settings.deep_research_auto_tools_enabled",
            False,
        )
        selector = ToolSelector()
        actions = await selector.select_tools(["tweet"])
        assert actions == []

    def test_classify_gap_lyrics__возвращает_genius(self) -> None:
        """_classify_gap('lyrics') → genius через GAP_TOOL_MAP."""
        tools = ToolSelector._classify_gap("lyrics")
        assert "genius" in tools
        assert "web_search_ddg" in tools

    def test_classify_gap_неизвестный__web_search_ddg(self) -> None:
        """Неизвестный пробел → fallback на web_search_ddg."""
        tools = ToolSelector._classify_gap("что-то-неизвестное-xyz")
        assert tools == ["web_search_ddg"]

    def test_resolve_gap_key_ключевые_слова(self) -> None:
        """_resolve_gap_key матчит keyword-группы."""
        # Tweet → social_media
        assert (
            ToolSelector._resolve_gap_key("tweet about something")
            == "missing_social_media"
        )
        # Twitter → social_media
        assert (
            ToolSelector._resolve_gap_key("twitter discussion")
            == "missing_social_media"
        )
        # News → news
        assert ToolSelector._resolve_gap_key("news about AI") == "missing_news"
        # Statistics → statistics
        assert (
            ToolSelector._resolve_gap_key("статистика продаж") == "missing_statistics"
        )
        # Documentation → documentation
        assert (
            ToolSelector._resolve_gap_key("api documentation")
            == "missing_documentation"
        )
        # Academic → academic
        assert (
            ToolSelector._resolve_gap_key("pubmed medical")
            == "missing_academic_sources"
        )
        # Unknown → None
        assert ToolSelector._resolve_gap_key("xyzabc") is None

    def test_select_tools_дедупликация(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Одинаковые gap'ы → дедупликация по (tool_name, query)."""
        monkeypatch.setattr(
            "src.core.rag.tool_selector.settings.deep_research_auto_tools_enabled", True
        )
        monkeypatch.setattr(
            "src.core.rag.tool_selector.settings.tool_selector_max_tools", 5
        )

        async def _run():
            selector = ToolSelector()
            actions = await selector.select_tools(["tweet", "tweet"])
            return actions

        import asyncio

        actions = asyncio.run(_run())
        tool_names = [a.tool_name for a in actions]
        # x_rss должен быть только один раз
        assert tool_names.count("x_rss") == 1


# ═══════════════════════════════════════════════════════════════════
# 5. MemorySeeder
# ═══════════════════════════════════════════════════════════════════


class TestMemorySeeder:
    """Тесты MemorySeeder.seed() — memory-seeded research context."""

    @pytest.mark.asyncio
    async def test_seed_отключён__пустой_контекст(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Флаг deep_research_memory_seed_enabled=False → пустой ResearchContext."""
        monkeypatch.setattr(
            "src.core.rag.memory_seed.settings.deep_research_memory_seed_enabled", False
        )
        seeder = MemorySeeder()
        ctx = await seeder.seed("запрос", telegram_id=123)
        assert ctx.prior_facts == []
        assert ctx.seed_prompt == ""
        assert ctx.related_entities == []

    @pytest.mark.asyncio
    async def test_seed_без_qdrant__пустой_контекст(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Qdrant не найден / эмбеддинг недоступен → prior_facts пуст."""
        monkeypatch.setattr(
            "src.core.rag.memory_seed.settings.deep_research_memory_seed_enabled", True
        )
        # Мокаем _get_embedding чтобы вернул None (эмбеддинг недоступен)
        seeder = MemorySeeder()

        with patch.object(seeder, "_get_embedding", AsyncMock(return_value=None)):
            ctx = await seeder.seed("запрос", telegram_id=123)

        assert ctx.prior_facts == []
        assert ctx.related_entities == []

    @pytest.mark.asyncio
    async def test_seed_с_prior_facts__контекст_заполнен(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Qdrant возвращает факты → prior_facts попадают в контекст."""
        monkeypatch.setattr(
            "src.core.rag.memory_seed.settings.deep_research_memory_seed_enabled", True
        )
        monkeypatch.setattr(
            "src.core.rag.memory_seed.settings.memory_seed_max_facts", 3
        )

        seeder = MemorySeeder()

        mock_vs = AsyncMock()
        mock_vs.search_similar_memories = AsyncMock(
            return_value=[
                {
                    "fact": "Пользователь интересуется Python",
                    "memory_id": 1,
                    "score": 0.92,
                },
                {"fact": "Предпочитает FastAPI", "memory_id": 2, "score": 0.85},
            ]
        )

        with (
            patch.object(
                seeder, "_get_embedding", AsyncMock(return_value=[0.1, 0.2, 0.3])
            ),
            patch(
                "src.core.actions.vector_store.get_vector_store",
                AsyncMock(return_value=mock_vs),
            ),
            patch(
                "src.core.memory.memory_recall.recall",
                AsyncMock(return_value=MagicMock(facts=[])),
            ),
        ):
            ctx = await seeder.seed("Python разработка", telegram_id=123)

        assert len(ctx.prior_facts) == 2
        assert ctx.prior_facts[0]["fact"] == "Пользователь интересуется Python"
        assert ctx.prior_facts[0]["score"] == 0.92
        assert "Prior knowledge from user's memory" in ctx.seed_prompt
        assert "Пользователь интересуется Python" in ctx.seed_prompt

    @pytest.mark.asyncio
    async def test_seed_с_memory_recall__related_entities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """memory_recall возвращает факты → related_entities заполнены."""
        monkeypatch.setattr(
            "src.core.rag.memory_seed.settings.deep_research_memory_seed_enabled", True
        )

        seeder = MemorySeeder()

        mock_recall_result = MagicMock()
        mock_recall_result.facts = [
            MagicMock(fact="FastAPI — отличный фреймворк"),
            MagicMock(fact="SQLAlchemy 2.0 поддерживает asyncio"),
        ]

        with (
            patch.object(seeder, "_get_embedding", AsyncMock(return_value=None)),
            patch(
                "src.core.memory.memory_recall.recall",
                AsyncMock(return_value=mock_recall_result),
            ),
        ):
            ctx = await seeder.seed("ORM в Python", telegram_id=123)

        assert len(ctx.related_entities) == 2
        assert "FastAPI" in ctx.related_entities[0]
        assert "Related entities:" in ctx.seed_prompt

    @pytest.mark.asyncio
    async def test_seed_исключения_graceful_degradation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """При падении get_vector_store или recall — возвращается частичный контекст."""
        monkeypatch.setattr(
            "src.core.rag.memory_seed.settings.deep_research_memory_seed_enabled", True
        )

        seeder = MemorySeeder()

        with (
            patch.object(
                seeder,
                "_get_embedding",
                AsyncMock(return_value=[0.1, 0.2]),
            ),
            patch(
                "src.core.actions.vector_store.get_vector_store",
                side_effect=RuntimeError("Qdrant offline"),
            ),
            patch(
                "src.core.memory.memory_recall.recall",
                side_effect=ConnectionError("recall unavailable"),
            ),
        ):
            ctx = await seeder.seed("любой запрос", telegram_id=123)

        # Ничего не упало, возвращён пустой контекст
        assert isinstance(ctx, ResearchContext)
        assert ctx.prior_facts == []
        assert ctx.related_entities == []
        assert ctx.seed_prompt == ""
