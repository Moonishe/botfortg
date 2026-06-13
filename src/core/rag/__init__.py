"""RAG pipeline — индексация документов и статей в Qdrant."""

from src.core.rag.chunker import chunk_text, ChunkConfig
from src.core.rag.document_store import (
    DocumentStore,
    DocumentRecord,
    get_document_store,
)
from src.core.rag.ingest import (
    ingest_file,
    ingest_directory,
    get_ingested_documents,
    delete_document,
    rebuild_index,
)
from src.core.rag.types import (
    ResearchRequest,
    ResearchResult,
    ResearchSource,
    ResearchStatus,
    ResearchTopic,
    SwarmSubTask,
    SwarmResult,
    ConsensusResult,
    KnowledgeClaim,
    ContradictionResult,
    ToolAction,
    ResearchContext,
    TemporalAssertion,
    TemporalEvent,
    TemporalContradiction,
    Timeline,
    ClaimEdgeType,
)
from src.core.rag.deep_research_pipeline import (
    DeepResearchPipeline,
    get_deep_research_pipeline,
)
from src.core.rag.swarm import SwarmOrchestrator
from src.core.rag.tool_selector import ToolSelector
from src.core.rag.timeline import TimelineExtractor
from src.core.rag.memory_seed import MemorySeeder
from src.core.rag.knowledge_graph import KnowledgeGraph
from src.core.rag.prompts import (
    CLARIFY_PROMPT,
    EXTRACT_CLAIMS_PROMPT,
    CROSS_REF_PROMPT,
    SYNTHESIS_PROMPT,
    EXTRACT_TIMELINE_PROMPT,
)

__all__ = [
    "CLARIFY_PROMPT",
    "CROSS_REF_PROMPT",
    "EXTRACT_CLAIMS_PROMPT",
    "EXTRACT_TIMELINE_PROMPT",
    "SYNTHESIS_PROMPT",
    "ChunkConfig",
    "ClaimEdgeType",
    "ConsensusResult",
    "ContradictionResult",
    "DeepResearchPipeline",
    "DocumentRecord",
    "DocumentStore",
    "KnowledgeClaim",
    "KnowledgeGraph",
    "MemorySeeder",
    "ResearchContext",
    "ResearchRequest",
    "ResearchResult",
    "ResearchSource",
    "ResearchStatus",
    "ResearchTopic",
    "SwarmOrchestrator",
    "SwarmResult",
    "SwarmSubTask",
    "TemporalAssertion",
    "TemporalContradiction",
    "TemporalEvent",
    "Timeline",
    "TimelineExtractor",
    "ToolAction",
    "ToolSelector",
    "chunk_text",
    "delete_document",
    "get_deep_research_pipeline",
    "get_document_store",
    "get_ingested_documents",
    "ingest_directory",
    "ingest_file",
    "rebuild_index",
]
