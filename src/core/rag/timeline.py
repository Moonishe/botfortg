"""Timeline extraction — извлечение временных утверждений и хронологической карты."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, UTC
from typing import Any, cast

from src.config import settings
from src.core.rag.types import (
    KnowledgeClaim,
    TemporalAssertion,
    TemporalEvent,
    TemporalContradiction,
    Timeline,
)

logger = logging.getLogger(__name__)

_DATE_PATTERNS = [
    (re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b"), "day"),
    (re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])\b"), "month"),
    (re.compile(r"\b(19|20)\d{2}\b"), "year"),
]


class TimelineExtractor:
    """Извлекает temporal assertions, строит хронологическую карту."""

    async def extract(
        self,
        claims: list[KnowledgeClaim],
        provider: Any = None,
    ) -> Timeline:
        """Извлечь таймлайн из claims."""
        timeline = Timeline(generated_at=datetime.now(UTC))

        if not claims or not settings.deep_research_timeline_enabled:
            return timeline

        # ── Phase 1: Regex-based extraction (fast, deterministic) ──
        assertions: list[TemporalAssertion] = []
        for c in claims:
            for pattern, granularity in _DATE_PATTERNS:
                for m in pattern.finditer(c.text):
                    assertions.append(
                        TemporalAssertion(
                            claim_id=c.claim_id,
                            text=c.text[:150],
                            date_str=m.group(0),
                            granularity=granularity,
                            confidence=0.7 if granularity != "year" else 0.4,
                        )
                    )

        # ── Phase 2: LLM-based extraction (catches fuzzy dates) ──
        if provider is not None and len(claims) > 2:
            llm_assertions = await self._extract_via_llm(claims, provider)
            assertions.extend(llm_assertions)

        if not assertions:
            return timeline

        # ── Build events ──
        events: list[TemporalEvent] = []
        for a in assertions:
            dt = self._parse_date(a.date_str)
            events.append(
                TemporalEvent(
                    assertion=a,
                    source_url="",
                    event_date=dt or datetime.min,
                    description=a.text[:200],
                )
            )

        # ── Phase 3: Detect temporal contradictions ──
        contradictions = await self._detect_contradictions(events, provider)

        timeline.events = events
        timeline.contradictions = contradictions
        timeline.chrono_map = self._build_chrono_map(events)
        return timeline

    async def _extract_via_llm(
        self,
        claims: list[KnowledgeClaim],
        provider: Any,
    ) -> list[TemporalAssertion]:
        """LLM-based date extraction for fuzzy dates."""
        from src.core.rag.prompts import EXTRACT_TIMELINE_PROMPT

        claims_text = "\n".join(
            f"[{i}] {c.text[:300]}" for i, c in enumerate(claims[:10])
        )
        prompt = EXTRACT_TIMELINE_PROMPT.format(claims_text=claims_text)

        try:
            messages = [{"role": "user", "content": prompt}]
            raw = await asyncio.wait_for(
                provider.chat(messages),
                timeout=45.0,
            )
            data = json.loads(raw) if isinstance(raw, str) else raw

            result: list[TemporalAssertion] = []
            # Edge guard: protect against malformed LLM JSON
            timeline_entries = (
                data.get("timeline", []) if isinstance(data, dict) else []
            )
            if not isinstance(timeline_entries, list):
                timeline_entries = []
            for item in timeline_entries:
                if not isinstance(item, dict):
                    continue
                idx = item.get("source_index", 0)
                claim_id = claims[idx].claim_id if 0 <= idx < len(claims) else ""
                result.append(
                    TemporalAssertion(
                        claim_id=claim_id,
                        text=item.get("description", ""),
                        date_str=item.get("date", ""),
                        granularity=item.get("granularity", "day"),
                        confidence=float(item.get("confidence", 0.5)),
                    )
                )
            return result
        except (json.JSONDecodeError, ValueError, TypeError, KeyError):
            logger.debug(
                "Timeline LLM extraction failed (malformed response)", exc_info=True
            )
            return []
        except Exception:
            logger.debug("Timeline LLM extraction failed", exc_info=True)
            return []

    async def _detect_contradictions(
        self,
        events: list[TemporalEvent],
        provider: Any,
    ) -> list[TemporalContradiction]:
        """Найти временные противоречия: перепутанный порядок, конфликтующие даты."""
        if not events or provider is None:
            return []

        # Simple heuristic: sort by date, check if earlier events
        # reference dates later than later events
        contradictions: list[TemporalContradiction] = []
        sorted_events = sorted(events, key=lambda e: e.event_date or datetime.min)
        for i in range(min(len(sorted_events), 20)):
            for j in range(i + 1, min(len(sorted_events), 20)):
                a = sorted_events[i]
                b = sorted_events[j]
                if self._event_contradicts(a, b):
                    contradictions.append(
                        TemporalContradiction(
                            event_a=a,
                            event_b=b,
                            contradiction_type="conflicting_date",
                            explanation=(
                                f"{a.assertion.date_str} vs {b.assertion.date_str}"
                            ),
                        )
                    )
        return contradictions[:10]

    def _build_chrono_map(
        self,
        events: list[TemporalEvent],
    ) -> dict[str, list[str]]:
        """Группировать события по декадам."""
        chrono: dict[str, list[str]] = {}
        for e in events:
            if e.event_date:
                decade = f"{e.event_date.year // 10 * 10}s"
                chrono.setdefault(decade, []).append(e.description[:100])
        return chrono

    @staticmethod
    def _parse_date(date_str: str) -> datetime | None:
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _event_contradicts(a: TemporalEvent, b: TemporalEvent) -> bool:
        if not a.event_date or not b.event_date:
            return False
        return a.event_date > b.event_date

    def export_markdown(self, timeline: Timeline) -> str:
        """Экспорт таймлайна в Markdown."""
        lines = ["## 📅 Хронология", ""]
        typed_events: list[TemporalEvent] = cast(list[TemporalEvent], timeline.events)
        sorted_events = sorted(typed_events, key=lambda e: e.event_date or datetime.min)
        current_year = None
        max_events = getattr(settings, "timeline_max_events", 50)
        for e in sorted_events[:max_events]:
            if not isinstance(e.event_date, datetime):
                continue
            if e.event_date and e.event_date.year != current_year:
                current_year = e.event_date.year
                lines.append(f"### {current_year}")
            lines.append(f"- **{e.assertion.date_str}** — {e.description[:120]}")
        if timeline.contradictions:
            lines.extend(["", "### ⚠️ Противоречия", ""])
            for tc in timeline.contradictions[:5]:
                lines.append(f"- {tc.explanation}")
        return "\n".join(lines)
