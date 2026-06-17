"""Temporal + semantic compression of memory facts."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, UTC
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.compaction.filters import non_task_memory_type_filter
from src.core.compaction.models import CompressResult
from src.core.infra.text_sanitizer import sanitize_html
from src.db.models import Memory, MemoryLink, User
from src.db.repos.memory_repo import add_memory

if TYPE_CHECKING:
    from src.core.actions.vector_store import VectorStore

logger = logging.getLogger(__name__)

_COMPRESS_SYSTEM = (
    "Ты — модуль сжатия памяти. Тебе даны факты об одном событии или теме. "
    "Сожми их в 1-2 лаконичных итоговых факта. Сохрани суть, убери временные детали. "
    'Верни ТОЛЬКО JSON: {"compressed": ["факт 1", "факт 2"], "confidence": 0.9}'
)

_DEFAULT_EMBEDDING_DIM = 128


async def _embed_texts(texts: list[str], llm_provider: Any) -> list[list[float]]:
    """Embed a batch of texts, falling back to zero vectors if embedding fails."""
    dim = int(
        getattr(settings, "embedding_dim", _DEFAULT_EMBEDDING_DIM)
        or _DEFAULT_EMBEDDING_DIM
    )
    if dim <= 0:
        dim = _DEFAULT_EMBEDDING_DIM

    if llm_provider is None:
        return [[0.0] * dim for _ in texts]

    try:
        if hasattr(llm_provider, "embed_batch"):
            batch = await llm_provider.embed_batch(texts)
            if batch and len(batch) == len(texts):
                return [
                    (emb if emb is not None and len(emb) == dim else [0.0] * dim)
                    for emb in batch
                ]
        if hasattr(llm_provider, "embed"):
            embeddings = await asyncio.gather(
                *[llm_provider.embed(text) for text in texts],
                return_exceptions=True,
            )
            return [
                (
                    emb
                    if not isinstance(emb, BaseException) and emb is not None
                    else [0.0] * dim
                )
                for emb in embeddings
            ]
    except Exception:
        logger.warning("Embedding compressed facts failed", exc_info=True)

    return [[0.0] * dim for _ in texts]


async def temporal_compress(
    session: AsyncSession,
    user_id: int,
    vector_store: VectorStore | None = None,
    *,
    min_group_size: int = 3,
    llm_provider: Any = None,
) -> CompressResult:
    """Group active facts by (contact_id, month) and compress groups with LLM."""
    result = CompressResult()

    user = await session.get(User, user_id)
    if user is None:
        return result

    max_groups = int(getattr(settings, "compaction_compress_max_groups", 10) or 10)

    # Load active, non-pinned, non-task facts for the user.
    # Cap the number of rows to avoid materializing huge tables in memory.
    max_candidates = max_groups * min_group_size * 2
    rows = await session.execute(
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.is_active.is_(True),
            Memory.pinned.is_(False),
            non_task_memory_type_filter(),
        )
        .order_by(Memory.contact_id, Memory.created_at)
        .limit(max_candidates)
    )
    memories = list(rows.scalars().all())

    # Group by (contact_id, month)
    groups: dict[tuple[int | None, str], list[Memory]] = {}
    for m in memories:
        month = m.created_at.strftime("%Y-%m") if m.created_at else "unknown"
        key = (m.contact_id, month)
        groups.setdefault(key, []).append(m)

    result.groups_examined = len(groups)

    # Select up to max_groups eligible groups; LLM calls are independent and run
    # in parallel so the DB transaction stays read-only during network I/O.
    eligible = [
        ((contact_id, _month), group)
        for (contact_id, _month), group in groups.items()
        if len(group) >= min_group_size
    ][:max_groups]

    compressed_texts = await asyncio.gather(
        *[_compress_group(group, llm_provider) for _, group in eligible],
        return_exceptions=True,
    )

    compressed_entries: list[tuple[Memory, str]] = []
    now = datetime.now(UTC)

    for ((contact_id, _month), group), compressed_text in zip(
        eligible, compressed_texts, strict=True
    ):
        if isinstance(compressed_text, BaseException) or not compressed_text:
            continue
        compressed_text = sanitize_html(compressed_text)
        if not compressed_text:
            continue

        # Create the compressed memory
        new_mem = await add_memory(
            session,
            user,
            fact=compressed_text,
            contact_id=contact_id,
            source="temporal_compressor",
            confidence=0.9,
            memory_type="personal" if contact_id is None else "contact_fact",
            decay_rate=0.02,
            deduplicate=False,
        )
        if new_mem is None:
            continue

        result.groups_compressed += 1
        result.facts_merged += len(group)
        compressed_entries.append((new_mem, compressed_text))

        # Deactivate old facts and link them as superseded by the new one
        for old in group:
            old.is_active = False
            old.validity_end = now
            old.updated_at = now
            link = MemoryLink(
                user_id=user_id,
                source_id=new_mem.id,
                target_id=old.id,
                relation_type="supersedes",
                weight=1.0,
            )
            session.add(link)
            result.facts_deactivated += 1

    await session.flush()
    # ponytail: commit before vector I/O to release SQLite writer lock;
    # Qdrant upsert is idempotent and can be retried next run if it fails.
    await session.commit()

    # Upsert compressed facts into Qdrant with real embeddings if possible
    if vector_store is not None and compressed_entries:
        texts = [text for _, text in compressed_entries]
        embeddings = await _embed_texts(texts, llm_provider)

        async def _upsert_one(
            new_mem: Memory, text: str, embedding: list[float]
        ) -> None:
            try:
                await vector_store.upsert_memory(
                    memory_id=new_mem.id,
                    user_id=user_id,
                    contact_id=new_mem.contact_id,
                    fact=text,
                    embedding=embedding,
                    importance=new_mem.importance or 0.5,
                    confidence=new_mem.confidence or 0.9,
                    created_at=new_mem.created_at.isoformat()
                    if new_mem.created_at
                    else None,
                )
            except Exception:
                logger.warning(
                    "Failed to upsert compressed memory %d to Qdrant",
                    new_mem.id,
                    exc_info=True,
                )

        await asyncio.gather(
            *(
                _upsert_one(new_mem, text, embedding)
                for (new_mem, text), embedding in zip(
                    compressed_entries, embeddings, strict=True
                )
            ),
            return_exceptions=True,
        )

    return result


async def _compress_group(
    facts: list[Memory],
    llm_provider: Any,
) -> str:
    """Ask LLM to compress a group of facts into one summary sentence."""
    if not facts:
        return ""

    if llm_provider is None:
        return ""

    lines = ["Факты:"]
    for f in facts:
        date_str = f.created_at.strftime("%d.%m") if f.created_at else ""
        lines.append(f"- {f.fact} ({date_str})")
    user_prompt = "\n".join(lines)

    try:
        if hasattr(llm_provider, "compress"):
            raw = await llm_provider.compress(_COMPRESS_SYSTEM, user_prompt)
        else:
            from src.llm.base import ChatMessage

            raw = await llm_provider.chat(
                [
                    ChatMessage(role="system", content=_COMPRESS_SYSTEM),
                    ChatMessage(role="user", content=user_prompt),
                ],
                task_type="MEMORY",
            )
    except Exception as exc:
        logger.warning("LLM compression failed for group: %s", exc)
        return ""

    if not raw:
        return ""

    text = str(raw).strip()
    # Try JSON extraction
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            compressed = parsed.get("compressed")
            if isinstance(compressed, list):
                parts = [str(c) for c in compressed if c]
                if parts:
                    return " ".join(parts)
                return ""  # empty list → skip compression
        except json.JSONDecodeError:
            pass
    # Fallback: strip markdown fences and return the first non-empty line
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    return text.split("\n")[0].strip()
