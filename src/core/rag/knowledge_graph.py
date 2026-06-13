"""Knowledge Graph — граф утверждений: поддержка, противоречие, цитирование."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.core.rag.types import ClaimEdgeType, KnowledgeClaim, ContradictionResult

logger = logging.getLogger(__name__)

VertexID = str  # claim_id


class KnowledgeGraph:
    """Асинхронный граф утверждений: детекция противоречий, confidence propagation."""

    def __init__(self) -> None:
        self._claims: dict[VertexID, KnowledgeClaim] = {}
        self._edges: list[tuple[VertexID, VertexID, ClaimEdgeType, float]] = []

    # ── Public API ──

    async def detect_contradictions(
        self,
        claims: list[KnowledgeClaim],
        provider: Any = None,
    ) -> list[ContradictionResult]:
        """Найти противоречия между утверждениями через LLM.

        Двухфазный подход: (1) pairwise cosine similarity text pre-filter O(n²),
        (2) LLM contradiction analysis для пар выше порога.
        """
        if not claims or provider is None or len(claims) < 2:
            return []

        from src.core.rag.prompts import CROSS_REF_PROMPT

        contradictions: list[ContradictionResult] = []

        # ── Phase 1: text similarity pre-filter ──
        pairs: list[tuple[int, int, float]] = []
        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
                sim = self._text_similarity(claims[i].text, claims[j].text)
                if sim > 0.3:
                    pairs.append((i, j, sim))
        pairs.sort(key=lambda x: x[2], reverse=True)

        # Limit to avoid O(n²) LLM calls
        max_pairs = 20
        pairs = pairs[:max_pairs]

        if not pairs:
            return []

        # ── Phase 2: LLM contradiction analysis ──
        claims_text = "\n".join(
            f"[{i}] {c.text[:200]} (source: {c.source_url})"
            for i, c in enumerate(claims)
        )
        prompt = CROSS_REF_PROMPT.format(claims_text=claims_text)

        try:
            messages = [{"role": "user", "content": prompt}]
            raw = await asyncio.wait_for(
                provider.chat(messages),
                timeout=60.0,
            )
            data = json.loads(raw) if isinstance(raw, str) else raw

            # Edge guard: protect against malformed LLM JSON
            contradiction_entries = (
                data.get("contradictions", []) if isinstance(data, dict) else []
            )
            if not isinstance(contradiction_entries, list):
                contradiction_entries = []

            for item in contradiction_entries:
                if not isinstance(item, dict):
                    continue
                idx_a = item.get("claim_a_idx", 0)
                idx_b = item.get("claim_b_idx", 0)
                if 0 <= idx_a < len(claims) and 0 <= idx_b < len(claims):
                    contradictions.append(
                        ContradictionResult(
                            claim_a=claims[idx_a],
                            claim_b=claims[idx_b],
                            edge_type=ClaimEdgeType.CONTRADICTS,
                            confidence=float(item.get("confidence", 0.5)),
                            explanation=item.get("explanation", ""),
                        )
                    )

            return contradictions
        except (json.JSONDecodeError, ValueError, TypeError, KeyError):
            logger.debug(
                "KG contradiction LLM call failed (malformed response)", exc_info=True
            )
            return contradictions
        except Exception:
            logger.debug("KG contradiction LLM call failed", exc_info=True)
            return contradictions

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """Простое cosine similarity на словах (без зависимостей)."""
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        return len(intersection) / min(len(words_a), len(words_b))
