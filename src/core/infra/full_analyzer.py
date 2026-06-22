"""
Full Analyzer — пакетный анализатор переписок.
Извлекает память, обязательства, напоминания из последних N сообщений
всех контактов из выбранных папок.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from src.db.session import get_session

logger = logging.getLogger(__name__)


@dataclass
class AnalysisProgress:
    """Прогресс анализа — передаётся в callback для UI."""

    phase: str = "init"
    current: int = 0
    total: int = 0
    contact_name: str = ""
    message: str = ""


@dataclass
class AnalysisResult:
    """Результат полного анализа."""

    contacts_processed: int = 0
    messages_scanned: int = 0
    memories_found: int = 0
    commitments_found: int = 0
    contradictions_found: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)


async def list_memories(session, user, *, contact_id=None):
    """Локальная обёртка — импортирует repo.list_memories."""
    from src.db.repo import list_memories as _list_memories

    return await _list_memories(session, user, contact_id=contact_id)


async def run_full_analysis(
    owner_id: int,
    provider,
    *,
    client=None,
    message_limit: int = 500,
    folder_names: Sequence[str] | None = None,
    contact_ids: list[int] | None = None,
    progress_callback=None,
    progress_message=None,
    include_photos: bool = False,
) -> AnalysisResult:
    """
    Полный анализ всех контактов из выбранных папок.

    Args:
        owner_id: Telegram ID владельца
        provider: LLMProvider для извлечения фактов
        message_limit: сколько последних сообщений анализировать (0 = все)
        folder_names: список папок для анализа (None = все)
        contact_ids: список peer_id для анализа (если задан, folder_names игнорируется)
        progress_callback: async callable(AnalysisProgress) для UI-обновлений
        progress_message: aiogram Message для progress_tracker (per‑contact)
        include_photos: если True, photo-сообщения помечаются [фото] в транскрипте
    """
    result = AnalysisResult()

    from src.db.repo import (
        get_or_create_user,
        list_contacts,
        find_similar_memories,
    )
    from src.core.memory.memory_extractor import extract_and_save_memories
    from src.core.actions.commitment_extractor import extract_and_save_commitments

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        # Получить контакты
        contacts = await list_contacts(
            session,
            owner,
            kinds=("user",),
            include_bots=False,
        )

    # Фильтр по ID контактов (приоритетнее folder_names)
    if contact_ids:
        id_set = set(contact_ids)
        contacts = [c for c in contacts if c.peer_id in id_set]
        if not contacts:
            result.details.append("Ни один из указанных контактов не найден.")
            return result

    # Фильтр по папкам (fuzzy matching, ~25% tolerance)
    if folder_names and not contact_ids:
        from rapidfuzz import fuzz

        FUZZY_THRESHOLD = 70  # ~25% допустимых ошибок
        filtered = []
        for c in contacts:
            cf = (c.folder_names or "").split(",")
            cf = [f.strip().lower() for f in cf if f.strip()]
            if not cf:
                continue
            for user_folder in folder_names:
                user_lower = user_folder.strip().lower()
                best = max(fuzz.ratio(user_lower, f) for f in cf)
                if best >= FUZZY_THRESHOLD:
                    filtered.append(c)
                    break
        contacts = filtered

    total = len(contacts)
    if total == 0:
        result.details.append("Нет контактов для анализа.")
        return result

    if progress_callback:
        await progress_callback(
            AnalysisProgress(
                phase="scan",
                total=total,
                message=f"Найдено {total} контактов",
            ),
        )

    # ponytail: parallel processing with semaphore — 5x speedup for 35 contacts.
    # Upgrade: make concurrency configurable via settings if API rate limits vary.
    _sem = asyncio.Semaphore(5)
    _counter = 0
    _counter_lock = asyncio.Lock()

    async def _process_one(contact, idx: int) -> None:
        nonlocal _counter
        contact_name = contact.display_name or str(contact.peer_id)
        async with _sem:
            async with _counter_lock:
                _counter += 1
                current = _counter
            if progress_callback:
                await progress_callback(
                    AnalysisProgress(
                        phase="processing",
                        current=current,
                        total=total,
                        contact_name=contact_name,
                        message=f"Анализ {contact_name}...",
                    )
                )
            try:
                # message_limit=0 means ALL messages
                effective_limit = message_limit if message_limit > 0 else 100000

                if client:
                    from src.core.contacts.chat_service import load_chat

                    messages = await load_chat(
                        client,
                        owner_id,
                        contact.peer_id,
                        limit=effective_limit,
                        transcribe=False,
                        incremental=False,
                    )
                else:
                    async with get_session() as session:
                        from src.db.repo import fetch_chat_messages

                        owner_synced = await session.merge(owner) if session else owner
                        messages = await fetch_chat_messages(
                            session,
                            owner_synced,
                            contact.peer_id,
                            limit=effective_limit,
                        )

                if not messages:
                    result.details.append(f"{contact_name}: нет сообщений")
                    return

                # Filter photo-only messages if not include_photos
                if not include_photos:
                    messages = [
                        m
                        for m in messages
                        if getattr(m, "kind", "text") != "photo"
                        or getattr(m, "text", None)
                        or getattr(m, "transcript", None)
                        or getattr(m, "extracted_text", None)
                    ]

                result.contacts_processed += 1
                result.messages_scanned += len(messages)

                # Chunking: split large transcripts into chunks of 50 messages
                # to avoid token overflow. Each chunk → separate LLM call.
                CHUNK_SIZE = 50
                total_mem_count = 0
                if len(messages) <= CHUNK_SIZE:
                    try:
                        mem_count = await extract_and_save_memories(
                            provider,
                            owner_id,
                            contact,
                            messages,
                        )
                        total_mem_count = mem_count
                    except Exception as e:
                        logger.exception(
                            "Memory extraction failed for %s", contact_name
                        )
                        result.errors.append(f"Память {contact_name}: {e}")
                else:
                    # Process chunks in parallel within this contact
                    chunk_tasks = []
                    for i in range(0, len(messages), CHUNK_SIZE):
                        chunk = messages[i : i + CHUNK_SIZE]
                        chunk_tasks.append(
                            extract_and_save_memories(
                                provider, owner_id, contact, chunk
                            )
                        )
                    try:
                        chunk_results = await asyncio.gather(
                            *chunk_tasks, return_exceptions=True
                        )
                        for cr in chunk_results:
                            if isinstance(cr, int):
                                total_mem_count += cr
                            elif isinstance(cr, Exception):
                                logger.warning("Chunk extraction error: %s", cr)
                    except Exception as e:
                        logger.exception(
                            "Chunked extraction failed for %s", contact_name
                        )
                        result.errors.append(f"Память {contact_name}: {e}")

                result.memories_found += total_mem_count
                if total_mem_count > 0:
                    result.details.append(f"{contact_name}: +{total_mem_count} фактов")

                try:
                    async with get_session() as session:
                        owner_obj = await get_or_create_user(session, owner_id)
                        saved = await extract_and_save_commitments(
                            provider,
                            telegram_id=owner_obj.telegram_id,
                            contact_name=contact_name,
                            contact_peer_id=contact.peer_id,
                            messages=messages,
                        )
                        commit_count = len(saved)
                        result.commitments_found += commit_count
                        if commit_count > 0:
                            result.details.append(
                                f"{contact_name}: +{commit_count} обязательств"
                            )
                except Exception as e:
                    logger.exception(
                        "Commitment extraction failed for %s", contact_name
                    )
                    result.errors.append(f"Обязательства {contact_name}: {e}")

            except Exception as e:
                logger.exception("Analysis failed for %s", contact_name)
                result.errors.append(f"{contact_name}: {e}")

    # Run all contacts in parallel with concurrency limit
    tasks = [_process_one(c, i) for i, c in enumerate(contacts)]
    await asyncio.gather(*tasks)

    if progress_callback:
        await progress_callback(
            AnalysisProgress(
                phase="done",
                total=total,
                message="Анализ завершён",
            ),
        )

    return result


def format_analysis_report(result: AnalysisResult) -> str:
    """Формирует красивый HTML-отчёт."""
    lines = [
        "🧠 <b>Полный анализ завершён</b>",
        "",
        f"👥 Контактов: <b>{result.contacts_processed}</b>",
        f"💬 Сообщений: <b>{result.messages_scanned}</b>",
        f"🧩 Фактов в память: <b>{result.memories_found}</b>",
        f"📝 Обязательств: <b>{result.commitments_found}</b>",
        f"⚠️ Противоречий: <b>{result.contradictions_found}</b>",
    ]

    if result.details:
        lines.append("")
        lines.append("<b>Детали:</b>")
        for d in result.details[:20]:
            lines.append(f"  • {d}")
        if len(result.details) > 20:
            lines.append(f"  ... и ещё {len(result.details) - 20}")

    if result.errors:
        lines.append("")
        lines.append(f"<b>Ошибки ({len(result.errors)}):</b>")
        for e in result.errors[:5]:
            lines.append(f"  ❌ {e}")

    return "\n".join(lines)
