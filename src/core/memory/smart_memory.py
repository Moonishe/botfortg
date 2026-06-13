"""Smart memory extraction after sync — verifies dates, avoids confusion, deduplicates.

After sync completes, this module:
1. Extracts facts about the OWNER from their own messages (contact_id=NULL)
2. Extracts facts about each CONTACT from conversations (contact_id=peer_id)
3. Verifies dates — skips facts with future dates or dates >1 year old
4. Deduplicates — uses text similarity against existing memories
5. Shows progress per contact
"""

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, UTC

from src.core.contacts.chat_service import messages_to_transcript
from src.core.memory.memory_extractor import MEMORIES_SYSTEM, _parse_json_array
from src.core.memory.memory_queue import MemoryJob, enqueue
from src.db.repo import fetch_chat_messages, get_or_create_user, list_memories
from src.db.session import get_session
from src.llm.base import ChatMessage, LLMProvider, TaskType
from src.core.memory.pending_questions import add_question

logger = logging.getLogger(__name__)

# Cap concurrent LLM calls to avoid hammering the provider when many
# contacts are processed in parallel. Each contact triggers up to 2 calls
# (owner facts + contact facts); the gather below issues them concurrently
# per contact, so the semaphore bounds the absolute fan-out.
_MAX_CONCURRENT_LLM = 4
_llm_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_LLM)

# Timeout for acquiring the LLM semaphore. If all 4 slots are occupied by
# hanging LLM calls, this prevents the remaining tasks from waiting forever.
_SEMAPHORE_ACQUIRE_TIMEOUT: float = 120.0  # 2 minutes

# Simple TTL cache for already-extracted facts to avoid redundant LLM
# calls during batch processing.  Key: "{owner_id}:{contact_id_or_0}:{transcript_hash}".
# Transcript hash prevents owner-facts from different conversations
# (contact=None → both get the same contact_id=0 part) from colliding.
# Bounded at _FACT_CACHE_MAX entries — oldest entries are evicted when
# the limit is exceeded. Stale entries (past _FACT_CACHE_TTL) are also
# lazily cleaned on key re-access.
_FACT_CACHE_TTL: float = 300.0  # 5 minutes
_FACT_CACHE_MAX: int = 200
_fact_cache: dict[str, tuple[float, list[dict[str, object]]]] = {}


def _evict_fact_cache_if_full() -> None:
    """Evict oldest entries when the fact cache exceeds its max size."""
    if len(_fact_cache) > _FACT_CACHE_MAX:
        # Sort by timestamp (ascending) and remove oldest 25%
        stale = sorted(_fact_cache.items(), key=lambda x: x[1][0])[
            : _FACT_CACHE_MAX // 4
        ]
        for key, _ in stale:
            _fact_cache.pop(key, None)


# Regex для поиска дат в тексте факта
_DATE_PATTERNS = [
    re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})"),  # DD.MM.YYYY  DD/MM/YY
    re.compile(r"(\d{4})[-](\d{1,2})[-](\d{1,2})"),  # YYYY-MM-DD
    re.compile(
        r"(\d{1,2})\s+(январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])\s+(\d{4})",
        re.IGNORECASE,
    ),  # "15 мая 2024"
    re.compile(r"(\d{4})\s+года?"),  # "2024 года" — год в конце
]

_MONTH_MAP = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
    "январь": 1,
    "февраль": 2,
    "март": 3,
    "апрель": 4,
    "май": 5,
    "июнь": 6,
    "июль": 7,
    "август": 8,
    "сентябрь": 9,
    "октябрь": 10,
    "ноябрь": 11,
    "декабрь": 12,
}

# Порог схожести для дедупликации
_DEDUP_SIMILARITY_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


async def _detect_ambiguous_contacts(
    owner_id: int,
    contact_ids: list[int],
    name_map: dict[int, str],
    owner,
) -> None:
    """Detect potentially ambiguous contacts and queue questions (Feature 1).

    Checks:
    1. Contacts with suspicious names ("Unknown", empty, numeric-only)
    2. Contacts with very similar display names (potential duplicates)
    """
    suspicious_names: list[tuple[int, str]] = []
    names_for_comparison: list[tuple[int, str]] = []

    for pid in contact_ids:
        name = name_map.get(pid, str(pid))
        stripped = name.strip()

        # Empty or generic names
        if not stripped or stripped.lower() in (
            "unknown",
            "deleted account",
            "удалённый аккаунт",
        ):
            suspicious_names.append((pid, f'Контакт "{name}" — кто это?'))
            continue

        # Numeric-only names (likely phone numbers or raw IDs)
        if stripped.lstrip("+").isdigit() and len(stripped) >= 7:
            suspicious_names.append(
                (pid, f'Контакт "{name}" — это номер телефона? Уточни имя.')
            )
            continue

        names_for_comparison.append((pid, stripped))

    # Queue suspicious name questions
    for _pid, question in suspicious_names:
        await add_question(owner_id, question)
        logger.debug("Queued question for owner %d: %s", owner_id, question)

    # Detect similar names (potential duplicates) via name-based hash grouping
    def _simplify_name(n: str) -> str:
        return re.sub(r"[^a-zа-яё]", "", n.lower())

    name_groups: dict[str, list[str]] = {}
    for pid, name in names_for_comparison:
        key = _simplify_name(name)
        name_groups.setdefault(key, []).append(name)

    for key, group in name_groups.items():
        if len(group) > 1:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    name_a, name_b = group[i], group[j]
                    if name_a != name_b:
                        question = f'"{name_a}" и "{name_b}" — это один человек?'
                        await add_question(owner_id, question)
                        logger.debug(
                            "Queued similarity question for owner %d: %s",
                            owner_id,
                            question,
                        )


async def _process_single_contact(
    provider: LLMProvider,
    owner_id: int,
    peer_id: int,
    progress_callback=None,
    idx: int = 0,
    total: int = 0,
) -> dict:
    """Process one contact: load messages, extract facts, save, return stats.

    Designed for use with asyncio.gather — each invocation is independent
    and manages its own DB sessions.  The module-level _llm_semaphore
    (used inside _extract_llm_filtered) caps total concurrent LLM calls.
    """
    from src.db.repo import get_contact

    # ── Load contact & messages ──
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        contact = await get_contact(session, owner, peer_id)
    contact_name = contact.display_name if contact else str(peer_id)

    if progress_callback:
        await progress_callback(idx, total, contact_name, "processing", "")

    async with get_session() as session:
        messages = await fetch_chat_messages(session, owner, peer_id, limit=80)

    if not messages:
        if progress_callback:
            await progress_callback(idx, total, contact_name, "skip", "нет сообщений")
        return {
            "owner_facts": 0,
            "contact_facts": 0,
            "skipped": 0,
            "recent_facts": [],
        }

    transcript = messages_to_transcript(messages)

    # ── Extract owner + contact facts (concurrent, semaphore-limited) ──
    (owner_result, contact_result) = await asyncio.gather(
        _extract_llm_filtered(provider, owner_id, contact=None, transcript=transcript),
        _extract_llm_filtered(
            provider, owner_id, contact=contact, transcript=transcript
        ),
        return_exceptions=True,
    )

    recent: list[str] = []
    total_skipped = 0

    if isinstance(owner_result, BaseException):
        logger.warning(
            "Owner-facts extraction failed for peer %d: %s", peer_id, owner_result
        )
        owner_facts: list[dict] = []
        skipped_o = 0
    else:
        owner_facts, skipped_o = owner_result  # type: ignore[assignment]
    total_skipped += skipped_o
    if owner_facts:
        await _save_facts_to_queue(owner_id, contact_id=None, facts=owner_facts)
        for fact in owner_facts[:2]:
            recent.append(fact["fact"])

    if isinstance(contact_result, BaseException):
        logger.warning(
            "Contact-facts extraction failed for peer %d: %s", peer_id, contact_result
        )
        contact_facts: list[dict] = []
        skipped_c = 0
    else:
        contact_facts, skipped_c = contact_result  # type: ignore[assignment]
    total_skipped += skipped_c
    if contact_facts:
        contact_peer_id = contact.peer_id if contact else None
        await _save_facts_to_queue(
            owner_id, contact_id=contact_peer_id, facts=contact_facts
        )
        for fact in contact_facts[:2]:
            recent.append(fact["fact"])

    # ── Progress: done ──
    extra_parts = []
    if owner_facts:
        extra_parts.append(f"+{len(owner_facts)} о себе")
    if contact_facts:
        extra_parts.append(f"+{len(contact_facts)} о контакте")
    extra = ", ".join(extra_parts) if extra_parts else "0 фактов"
    if progress_callback:
        await progress_callback(idx, total, contact_name, "done", extra)

    return {
        "owner_facts": len(owner_facts),
        "contact_facts": len(contact_facts),
        "skipped": total_skipped,
        "recent_facts": recent,
    }


async def smart_extract_after_sync(
    owner_id: int,
    provider: LLMProvider,
    contact_ids: list[int],
    progress_callback=None,
    progress_message=None,
) -> dict:
    """Запускает smart-извлечение памяти после синхронизации.

    Args:
        owner_id: Telegram ID владельца.
        provider: LLM-провайдер.
        contact_ids: список peer_id контактов для анализа.
        progress_callback: async (idx, total, name, status, extra) -> None.
            status: 'pending' | 'processing' | 'done' | 'skip'
            extra: str — например "+3 факта" для done.
        progress_message: aiogram Message для progress_tracker (per‑contact).

    Returns:
        {"owner_facts": N, "contact_facts": M, "skipped_stale": K, "recent_facts": [...]}
    """
    total = len(contact_ids)

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)

    # Pre-build display-name map via batch query (avoid N sequential DB sessions)
    _name_map: dict[int, str] = {}
    if total > 0:
        from sqlalchemy import select
        from src.db.models._contacts import Contact

        async with get_session() as session:
            result = await session.execute(
                select(Contact.peer_id, Contact.display_name).where(
                    Contact.user_id == owner.id,
                    Contact.peer_id.in_(contact_ids),
                )
            )
            for peer_id, display_name in result:
                _name_map[peer_id] = display_name or str(peer_id)
        # Fill in any contacts not found in DB
        for pid in contact_ids:
            if pid not in _name_map:
                _name_map[pid] = str(pid)

    # ── Detect ambiguous contacts (Feature 1: question accumulation) ──
    await _detect_ambiguous_contacts(owner_id, contact_ids, _name_map, owner)

    # ── Parallel extraction across all contacts ──
    # Each per-contact task manages its own DB sessions; the module-level
    # _llm_semaphore caps total concurrent LLM calls at _MAX_CONCURRENT_LLM.
    tasks = [
        _process_single_contact(provider, owner_id, pid, progress_callback, i, total)
        for i, pid in enumerate(contact_ids)
    ]

    gather_results = await asyncio.gather(*tasks, return_exceptions=True)

    # ── Aggregate results ──
    total_owner_facts = 0
    total_contact_facts = 0
    total_skipped = 0
    _recent_facts: list[str] = []

    for result in gather_results:
        if isinstance(result, BaseException):
            logger.error("Contact-processing task failed: %s", result)
            continue
        total_owner_facts += result["owner_facts"]
        total_contact_facts += result["contact_facts"]
        total_skipped += result["skipped"]
        for fact_text in result["recent_facts"]:
            if len(_recent_facts) < 10:
                _recent_facts.append(fact_text)

    return {
        "owner_facts": total_owner_facts,
        "contact_facts": total_contact_facts,
        "skipped_stale": total_skipped,
        "recent_facts": _recent_facts,
    }


# ---------------------------------------------------------------------------
# Внутренние helpers
# ---------------------------------------------------------------------------


async def _extract_llm_filtered(
    provider: LLMProvider,
    telegram_id: int,
    contact,
    transcript: str,
) -> tuple[list[dict], int]:
    """Вызвать LLM, распарсить факты, отфильтровать по датам и дедуплицировать.

    Returns:
        (valid_facts, skipped_count)
    """
    if not transcript:
        return [], 0

    # ── Cache check (avoids redundant LLM calls within TTL window) ──
    # Include transcript hash so owner-facts from different conversations
    # are not incorrectly shared (contact=None → both get key "...:0").
    transcript_hash = hashlib.md5(transcript.encode()).hexdigest()[:12]
    cache_key = f"{telegram_id}:{contact.peer_id if contact else 0}:{transcript_hash}"
    cached = _fact_cache.get(cache_key)
    if cached is not None:
        cached_time, cached_facts = cached
        if time.monotonic() - cached_time < _FACT_CACHE_TTL:
            logger.debug("Cache hit for %s", cache_key)
            return cached_facts, 0
        else:
            _fact_cache.pop(cache_key, None)

    # Формируем промпт (как в memory_extractor.py)
    if contact is not None:
        user_prompt = (
            f"Собеседник: {contact.display_name}.\n"
            "Извлеки важные факты о собеседнике из этой переписки:\n\n"
            f"{transcript}"
        )
    else:
        user_prompt = (
            "Извлеки важные факты о пользователе (его предпочтения, личные данные, задачи) "
            "из этой переписки:\n\n"
            f"{transcript}"
        )

    try:
        # Acquire semaphore with timeout — prevents deadlock if all LLM
        # calls hang and consume every semaphore slot.
        try:
            await asyncio.wait_for(
                _llm_semaphore.acquire(), timeout=_SEMAPHORE_ACQUIRE_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning(
                "LLM semaphore acquire timed out after %.0fs — "
                "all %d slots may be occupied by hanging calls",
                _SEMAPHORE_ACQUIRE_TIMEOUT,
                _MAX_CONCURRENT_LLM,
            )
            return [], 0
        try:
            raw = await provider.chat(
                [
                    ChatMessage(role="system", content=MEMORIES_SYSTEM),
                    ChatMessage(role="user", content=user_prompt),
                ],
                task_type=TaskType.MEMORY,
            )
        finally:
            _llm_semaphore.release()
    except (ConnectionError, OSError, ValueError):
        logger.exception("Smart memory LLM call failed")
        return [], 0

    items = _parse_json_array(raw)
    if not items:
        return [], 0

    # Фильтруем и валидируем
    contact_id = contact.peer_id if contact else None
    valid: list[dict] = []
    skipped = 0

    # Batch-load memories once for dedup (avoids N separate DB queries)
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        all_memories = await list_memories(session, owner, contact_id=contact_id)

    # Precompute hash set once for all facts (avoids O(N*M) rebuild)
    existing_hashes = _build_fact_hashes(all_memories)

    for item in items:
        if not isinstance(item, dict):
            continue
        fact = (item.get("fact") or "").strip()
        if not fact:
            continue

        # --- Проверка дат ---
        if _has_invalid_date(fact):
            skipped += 1
            logger.debug("Skipped fact with invalid date: %r", fact[:80])
            continue

        # --- Дедупликация ---
        if await _is_duplicate(
            telegram_id,
            contact_id,
            fact,
            existing_memories=all_memories,
            existing_hashes=existing_hashes,
        ):
            skipped += 1
            logger.debug("Skipped duplicate fact: %r", fact[:80])
            continue

        # Валидация sentiment
        sentiment = item.get("sentiment")
        if sentiment not in {"positive", "negative", "neutral"}:
            sentiment = None

        # importance 1-10 → 0.0-1.0
        raw_importance = item.get("importance")
        if isinstance(raw_importance, (int, float)):
            importance = max(0.0, min(1.0, raw_importance / 10.0))
        else:
            importance = None

        decay_rate = item.get("decay_rate")
        if not isinstance(decay_rate, (int, float)):
            decay_rate = None

        memory_type = item.get("memory_type")
        VALID_MEMORY_TYPES = {
            "personal",
            "contact_fact",
            "relationship",
            "task",
            "preference",
            "temporary",
        }
        if memory_type not in VALID_MEMORY_TYPES:
            memory_type = None

        valid.append(
            {
                "fact": fact,
                "sentiment": sentiment,
                "source": "chat",
                "importance": importance,
                "decay_rate": decay_rate,
                "memory_type": memory_type,
                "relation_type": item.get("relation_type"),
                "relation_to_index": item.get("relation_to_index"),
            }
        )

    # ── Cache store (with eviction to prevent unbounded growth) ──
    _evict_fact_cache_if_full()
    _fact_cache[cache_key] = (time.monotonic(), valid)

    return valid, skipped


def _has_invalid_date(fact_text: str) -> bool:
    """Проверяет, есть ли в тексте факта невалидная дата.

    Считается невалидной:
    - дата в будущем
    - дата старше 1 года (устаревший факт)

    Факты без дат считаются валидными.
    """
    now = datetime.now(UTC)
    one_year_ago = now - timedelta(days=365)
    future_cutoff = now + timedelta(days=1)  # +1 день допуска (часовые пояса)

    found_dates = _extract_dates(fact_text)
    if not found_dates:
        return False  # нет дат — всё ок

    for d in found_dates:
        if d > future_cutoff:
            logger.debug("Future date %s in fact: %r", d.date(), fact_text[:60])
            return True  # будущая дата
        if d < one_year_ago:
            logger.debug("Stale date %s in fact: %r", d.date(), fact_text[:60])
            return True  # старше года

    return False


def _extract_dates(text: str) -> list[datetime]:
    """Извлекает даты из текста факта. Возвращает список datetime (UTC)."""
    found: list[datetime] = []

    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                dt = _parse_date_match(match)
                if dt is not None:
                    found.append(dt)
            except (ValueError, IndexError):
                continue

    return found


def _parse_date_match(match: re.Match) -> datetime | None:
    """Парсит одну regex-группу в datetime."""
    groups = match.groups()
    group_count = len(groups)
    group0_len = len(groups[0]) if group_count >= 1 else 0

    # DD.MM.YYYY или DD/MM/YY — первая группа короткая (1-2 цифры)
    if group_count == 3 and group0_len <= 2 and groups[1].isdigit():
        day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
        if year < 100:
            year += 2000
        if 1 <= day <= 31 and 1 <= month <= 12 and year >= 2000:
            return datetime(year, month, day, tzinfo=UTC)

    # YYYY-MM-DD — первая группа 4 цифры, вторая/третья тоже цифры
    if (
        group_count == 3
        and group0_len == 4
        and groups[1].isdigit()
        and groups[2].isdigit()
    ):
        year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
        if 1 <= month <= 12 and 1 <= day <= 31:
            return datetime(year, month, day, tzinfo=UTC)

    # Русская дата: "15 мая 2024" — вторая группа содержит буквы
    if group_count == 3 and not groups[1].isdigit():
        day = int(groups[0])
        month_name = groups[1].lower()
        year = int(groups[2])
        month = _MONTH_MAP.get(month_name)
        if month and 1 <= day <= 31 and year >= 2000:
            return datetime(year, month, day, tzinfo=UTC)

    # Просто год: "2024 года" — одна группа
    if group_count == 1:
        year = int(groups[0])
        if year >= 2000:
            return datetime(year, 1, 1, tzinfo=UTC)

    return None


def _build_fact_hashes(memories: list) -> set[str]:
    """Precompute MD5 hashes (first 10 words) for a list of memories. Call once, reuse for all facts."""
    hashes: set[str] = set()
    for mem in memories:
        if not mem.is_active or not mem.fact:
            continue
        e_norm = " ".join(mem.fact.lower().strip().split()[:10])
        hashes.add(hashlib.md5(e_norm.encode()).hexdigest())
    return hashes


async def _is_duplicate(
    telegram_id: int,
    contact_id: int | None,
    fact_text: str,
    existing_memories: list | None = None,
    existing_hashes: set[str] | None = None,
) -> bool:
    """Проверяет, есть ли похожий факт в БД (через hash)."""
    if existing_memories is None:
        async with get_session() as session:
            owner = await get_or_create_user(session, telegram_id)
            existing_memories = await list_memories(
                session, owner, contact_id=contact_id
            )

    fact_lower = fact_text.lower().strip()
    for mem in existing_memories:
        if not mem.is_active or not mem.fact:
            continue
        if fact_lower == mem.fact.lower().strip():
            return True

    # Hash-based fuzzy dedup (first 10 words normalized)
    norm = " ".join(fact_lower.split()[:10])
    f_hash = hashlib.md5(norm.encode()).hexdigest()

    # Use precomputed hashes if available, otherwise build once
    if existing_hashes is None:
        existing_hashes = _build_fact_hashes(existing_memories)

    if f_hash in existing_hashes:
        logger.debug(
            "Duplicate detected (hash): %r",
            fact_text[:60],
        )
        return True

    return False


async def _save_facts_to_queue(
    telegram_id: int,
    contact_id: int | None,
    facts: list[dict],
) -> None:
    """Сохраняет факты через очередь (memory_queue)."""
    if not facts:
        return

    # Embedding batch
    from src.llm.router import build_provider

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        provider = await build_provider(session, owner, task_type=TaskType.MEMORY)

    if provider:
        texts = [f["fact"] for f in facts]
        try:
            embeddings = await provider.embed_batch(texts)
        except Exception:
            logger.warning("Failed to embed batch of %d facts", len(texts))
            embeddings = [None] * len(texts)
        for idx, vf in enumerate(facts):
            if idx < len(embeddings) and embeddings[idx] is not None:
                vf["embedding"] = embeddings[idx]

    await enqueue(
        MemoryJob(
            telegram_id=telegram_id,
            contact_id=contact_id,
            facts=facts,
            job_type="save",
        )
    )
    logger.info(
        "Smart memory: enqueued %d facts for user %d, contact %s",
        len(facts),
        telegram_id,
        contact_id,
    )
