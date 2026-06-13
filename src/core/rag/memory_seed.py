"""Memory-Seeded Research — prior knowledge injection из Qdrant и memory_recall."""

from __future__ import annotations

import logging

from src.config import settings
from src.core.rag.types import ResearchContext

logger = logging.getLogger(__name__)


class MemorySeeder:
    """Формирует ResearchContext из prior knowledge пользователя."""

    async def _get_embedding(self, text: str, telegram_id: int) -> list[float] | None:
        """Получить эмбеддинг для текстового запроса через LLM-провайдера."""
        try:
            from src.core.rag._provider import get_rag_provider

            provider = await get_rag_provider(
                purpose="background", telegram_id=telegram_id
            )
            if provider is None:
                return None
            return await provider.embed(text[:1000])
        except Exception:
            logger.debug("MemorySeed: embedding provider unavailable", exc_info=True)
            return None

    async def seed(
        self,
        query: str,
        telegram_id: int,
    ) -> ResearchContext:
        """Собрать prior knowledge из Qdrant и memory_recall.

        Args:
            query: Исследовательский запрос
            telegram_id: ID пользователя Telegram

        Returns:
            ResearchContext с prior фактами и seed-промптом
        """
        ctx = ResearchContext()

        if not settings.deep_research_memory_seed_enabled:
            return ctx

        # ── Vector search in Qdrant ──
        try:
            from src.core.actions.vector_store import get_vector_store

            embedding = await self._get_embedding(query, telegram_id)
            if embedding is None:
                logger.debug("MemorySeed: skip vector search — no embedding")
            else:
                vs = await get_vector_store()
                hits = await vs.search_similar_memories(
                    user_id=telegram_id,
                    embedding=embedding,
                    threshold=0.6,
                    limit=settings.memory_seed_max_facts,
                )
                for hit in hits:
                    ctx.prior_facts.append(
                        {
                            "fact": str(hit.get("fact", "")),
                            "memory_id": int(hit.get("memory_id", 0)),
                            "score": float(hit.get("score", 0.0)),
                        }
                    )
                logger.debug(
                    "MemorySeed: %d facts from vector store", len(ctx.prior_facts)
                )
        except Exception:
            logger.debug("MemorySeed: vector store unavailable", exc_info=True)

        # ── Memory recall (deep) ──
        try:
            from src.core.memory.memory_recall import recall

            recalled = await recall(
                telegram_id=telegram_id,
                query=query,
                mode="deep",
                limit=3,
            )
            for fact in recalled.facts:
                ctx.related_entities.append(fact.fact[:120])
        except Exception:
            logger.debug("MemorySeed: memory_recall unavailable", exc_info=True)

        # ── Build seed prompt ──
        parts: list[str] = []
        if ctx.prior_facts:
            facts_text = "\n".join(f"- {f['fact'][:200]}" for f in ctx.prior_facts[:3])
            parts.append(f"Prior knowledge from user's memory:\n{facts_text}")
        if ctx.related_entities:
            entities_text = "; ".join(ctx.related_entities[:5])
            parts.append(f"Related entities: {entities_text}")

        ctx.seed_prompt = "\n\n".join(parts)
        return ctx
