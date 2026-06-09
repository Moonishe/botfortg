"""Phase 1 & Phase 3 context-gathering functions — extracted from maestro.process().

Each function is a standalone async function with an explicit signature.
All try/except + logger.debug + return-on-error behaviour is preserved as-is.
Lazy imports are kept inline exactly as they were in the original closures.
"""

from __future__ import annotations

import logging
from typing import Any

from src.db.repo import get_or_create_user
from src.db.session import get_session

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 1 — 9 fully independent context sources
# ═══════════════════════════════════════════════════════════════════════════


async def _fetch_rag(
    owner_id: int | None,
    user_text: str,
    rag_enabled: bool,
    provider: Any,
) -> str:
    """S2 RAG: релевантный контекст из истории переписок."""
    if rag_enabled and owner_id is not None:
        _owner_db_id = None
        try:
            async with get_session() as session:
                owner_db = await get_or_create_user(session, owner_id)
                _owner_db_id = owner_db.id if owner_db else None
            if _owner_db_id is not None:
                from src.core.actions.vector_store import get_vector_store

                query_vec = await provider.embed(user_text)
                hits = await (await get_vector_store()).search(
                    user_id=_owner_db_id, embedding=query_vec, limit=5
                )
            else:
                hits = []
            if hits:
                rag_lines = []
                for h in hits:
                    prefix = f"[{h.peer_name}]" if h.peer_name else ""
                    rag_lines.append(f"{prefix} {h.text[:200]}")
                return "\n".join(rag_lines)
        except Exception:
            logger.debug("RAG search non-critical fail", exc_info=True)
    return ""


async def _fetch_persona(owner_id: int | None) -> str:
    """S3a persona блок."""
    if owner_id is not None:
        try:
            from src.core.intelligence.adaptive_persona import (
                format_persona_for_prompt,
            )

            return await format_persona_for_prompt(owner_id) or ""
        except Exception:
            logger.debug("Failed to format persona for prompt", exc_info=True)
    return ""


async def _fetch_style(owner_id: int | None) -> str:
    """S3b style‑match блок (динамический анализ стиля пользователя)."""
    if owner_id is not None:
        try:
            from src.core.intelligence.style_matcher import (
                get_or_update_style_profile,
            )

            return await get_or_update_style_profile(owner_id) or ""
        except Exception:
            logger.debug("Style matcher skipped", exc_info=True)
    return ""


async def _fetch_rules(owner_id: int | None) -> list:
    """S3c confirmed rules."""
    if owner_id is not None:
        try:
            from src.core.intelligence.adaptive_instructions import get_active_rules

            return await get_active_rules(owner_id)
        except Exception:
            logger.debug("Failed to load active rules", exc_info=True)
    return []


async def _fetch_anti_ai(owner_id: int | None) -> bool:
    """S3d anti-AI setting."""
    if owner_id is not None:
        try:
            async with get_session() as _s:
                _owner = await get_or_create_user(_s, owner_id)
                return _owner.settings.anti_ai_enabled
        except Exception:
            logger.debug("Failed to load anti_ai setting", exc_info=True)
    return False


async def _fetch_corrections(owner_id: int | None) -> str:
    """S3e recent corrections for context injection."""
    if owner_id is not None:
        try:
            from src.core.intelligence.correction_learner import (
                get_recent_corrections,
            )

            corrections = await get_recent_corrections(owner_id, limit=3)
            if corrections:
                return "; ".join(
                    f'"{c["original"][:80]}" → "{c["corrected"][:80]}"'
                    for c in corrections
                )
        except Exception:
            logger.debug("Failed to load correction context", exc_info=True)
    return ""


async def _fetch_transcription(owner_id: int | None) -> dict | None:
    """S3f voice transcription metadata."""
    if owner_id is not None:
        try:
            from src.core.memory.conversation_context import (
                get_and_clear_transcription_meta,
            )

            return await get_and_clear_transcription_meta(owner_id)
        except Exception:
            logger.debug("Failed to load transcription_meta", exc_info=True)
    return None


async def _fetch_dsm() -> str:
    """S3m DSM: cross-session project memory."""
    try:
        from src.core.intelligence.dsm import dsm_get_recent

        dsm_entries = await dsm_get_recent(limit=5)
        if dsm_entries:
            return "[ПРОЕКТНАЯ ПАМЯТЬ]\n" + "\n".join(
                f"- [{r['tags'] or 'общее'}] {r['content'][:200]}" for r in dsm_entries
            )
    except Exception:
        logger.debug("Failed to load DSM context", exc_info=True)
    return ""


async def _fetch_contact_graph(owner_id: int | None) -> str:
    """S3n contact graph: cross-contact relationship graph."""
    if owner_id is not None:
        try:
            from src.core.memory.memory_neighbors import get_contact_graph

            graph = await get_contact_graph(owner_id, limit=20)
            if graph.get("edges"):
                lines = []
                for edge in graph["edges"]:
                    lines.append(f"{edge['from']} ↔ {edge['to']} ({edge['relation']})")
                return "\n".join(lines)
        except Exception:
            logger.debug("Failed to build contact graph", exc_info=True)
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 — 4 ctx-attribute setters (parallel, disjoint attributes)
# ═══════════════════════════════════════════════════════════════════════════


async def _set_skill_index(
    owner_id: int | None,
    user_text: str,
    ctx: Any,
) -> list:
    """S3h skill_index — builds and injects skill index into ctx."""
    if owner_id is not None:
        try:
            from src.core.intelligence.skills import build_skill_index

            skill_str, skill_meta = await build_skill_index(
                owner_id, user_text, "maestro"
            )
            ctx.skill_index = skill_str
            return skill_meta
        except Exception:
            logger.debug("Failed to build skill index", exc_info=True)
    return []


async def _set_frozen(
    owner_id: int | None,
    user_text: str,
    ctx: Any,
) -> bool:
    """S3i frozen memory snapshot: top-3 facts pre-loaded."""
    if owner_id is not None:
        try:
            from src.core.memory.memory_recall import recall

            _recall_result = await recall(
                telegram_id=owner_id,
                query=user_text,
                limit=3,
                include_deep=False,
                mode="normal",
            )
            if _recall_result.facts:
                _lines = [
                    "[ПАМЯТЬ] Ниже факты о пользователе и его контактах. "
                    "Используй их ЕСТЕСТВЕННО в ответе — не перечисляй списком, "
                    "не говори «я помню» или «по моим данным». "
                    "Вплетай в речь как само собой разумеющееся."
                ]
                for _f in _recall_result.facts:
                    _lines.append(f"[{_f.reason}] {_f.fact}")
                ctx.frozen_snapshot = "\n".join(_lines)

                # Also update the frozen_provider so ContextEngine can serve it
                try:
                    from src.core.context.providers.frozen_provider import (
                        frozen_provider,
                    )

                    await frozen_provider.set_frozen(
                        owner_id,
                        [
                            {"fact": f"[{_f.reason}] {_f.fact}"}
                            for _f in _recall_result.facts
                        ],
                    )
                except Exception:
                    logger.debug("Failed to set frozen provider", exc_info=True)

                return True
        except Exception:
            logger.debug("Frozen snapshot recall failed, skipping", exc_info=True)
    return False


async def _gather_context(
    user_text: str,
    owner_id: int | None,
    contact_id: int | None = None,
) -> list:
    """S3j ContextEngine: pluggable context providers."""
    if owner_id is not None:
        try:
            from src.core.context.engine import engine as context_engine

            return await context_engine.gather(
                user_text,
                telegram_id=owner_id,
                contact_id=contact_id,
                limit=6,
            )
        except Exception:
            logger.debug("ContextEngine gather failed, skipping", exc_info=True)
    return []


async def _set_contact_rules(
    owner_id: int | None,
    contact_id: int | None,
    ctx: Any,
) -> None:
    """S3l contact-specific rules (pre-load for prompt injection)."""
    if contact_id and contact_id > 0 and owner_id is not None:
        try:
            from src.core.contacts.contact_rules import get_contact_rules_block

            _block = await get_contact_rules_block(owner_id, contact_id)
            if _block:
                ctx.contact_rules_block = _block
        except Exception:
            logger.debug("Failed to load contact rules block", exc_info=True)
