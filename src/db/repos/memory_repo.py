"""Memory repository — Memory, MemoryLink, MemoryCluster, MemoryCandidate, FTS."""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, distinct, func, or_, select, text as sql_text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from src.db.models import (
    Commitment,
    Contact,
    Memory,
    MemoryCandidate,
    MemoryCluster,
    MemoryClusterMember,
    MemoryLink,
    MemoryVersion,
)
from typing import TYPE_CHECKING

from src.core.contacts.contact_memory_digest import invalidate_contact_digest

if TYPE_CHECKING:
    from src.core.actions.vector_store import VectorStore

logger = logging.getLogger(__name__)


# ── Whitelist допустимых relation_type для MemoryLink ────────────────
# Используется в link_memories() для отсева LLM-галлюцинаций вроде
# «supersede» (без 's') или «replaces». Полный список должен совпадать с
# RELATION_EMOJI.keys() в src/core/memory/memory_chain.py и с LLM-промптом
# MEMORIES_SYSTEM в src/core/memory/memory_extractor.py.
# Канонические константы: src.core.memory.relation_types.RelationType.
_VALID_RELATION_TYPES: frozenset[str] = frozenset(
    {
        "cause",
        "effect",
        "contradicts",
        "supports",
        "continues",
        "example_of",
        "supersedes",
        "co_temporal",
        "co_entity",
        "preceded",
    }
)


@dataclass
class FtsHit:
    user_id: int
    peer_id: int
    message_id: int
    sender_name: str | None
    snippet: str
    rank: float
    peer_name: str | None = None
    date: datetime | None = None


# ── Морфологическая экспансия русских слов ────────────────────────────
# Расширяет поисковые запросы вариантами словоформ для повышения recall
# в FTS5 без внешних зависимостей (pymorphy2).
# ACTION: add FTS5 tokenizer for CJK (Chinese/Japanese/Korean) — requires ICU extension or separate index.
# Текущая токенизация CJK + кириллицы через 'L* N* Co' категории (см. alembic/versions/...)
_RU_MORPH_EXPANSIONS: dict[str, list[str]] = {
    "купил": ["купил", "купила", "купить", "покупал", "покупала", "покупать"],
    "работа": [
        "работа",
        "работаю",
        "работаешь",
        "работает",
        "работал",
        "работала",
        "работать",
    ],
    "жив": ["живу", "живешь", "живет", "жил", "жила", "жить"],
    "любл": ["люблю", "любишь", "любит", "любил", "любила", "любить"],
    "хочу": ["хочу", "хочешь", "хочет", "хотел", "хотела", "хотеть"],
    "могу": ["могу", "можешь", "может", "мог", "могла", "мочь"],
    "знаю": ["знаю", "знаешь", "знает", "знал", "знала", "знать"],
    "говор": [
        "говорю",
        "говоришь",
        "говорит",
        "говорил",
        "говорила",
        "говорить",
        "скажи",
        "сказать",
        "сказал",
        "сказала",
    ],
    "дела": [
        "делаю",
        "делаешь",
        "делает",
        "делал",
        "делала",
        "делать",
        "сделал",
        "сделала",
        "сделать",
    ],
    "ид": ["иду", "идешь", "идет", "шел", "шла", "идти", "пойду", "пойдешь", "пойти"],
    "есть": ["ем", "ешь", "ест", "ел", "ела", "есть", "поел", "поела", "поесть"],
    "смотр": [
        "смотрю",
        "смотришь",
        "смотрит",
        "смотрел",
        "смотрела",
        "смотреть",
        "посмотрел",
        "посмотреть",
    ],
    "дума": [
        "думаю",
        "думаешь",
        "думает",
        "думал",
        "думала",
        "думать",
        "подумал",
        "подумать",
    ],
    "поним": [
        "понимаю",
        "понимаешь",
        "понимает",
        "понимать",
        "понял",
        "поняла",
        "понять",
    ],
}


def _try_expand_russian_word(word: str) -> str | None:
    """Возвращает FTS5-выражение с морфологическими вариантами или None.

    Ищет *word* в _RU_MORPH_EXPANSIONS по корню:
    — если корень (ключ) содержится в *word*
    — ИЛИ *word* содержится в одном из вариантов
    Возвращает строку вида: ``("вариант1" OR "вариант2" OR ...)``
    Если совпадений нет — возвращает None.
    """
    word_lower = word.lower()
    for stem, variants in _RU_MORPH_EXPANSIONS.items():
        if stem in word_lower or any(word_lower in v for v in variants):
            quoted = " OR ".join(f'"{v}"' for v in variants)
            return f"({quoted})"
    return None


def _escape_fts_query(text: str) -> str:
    """Экранирует спецсимволы FTS5 и удаляет операторные токены.

    Заменяет кавычки, скобки, двоеточия, ^, * на пробелы,
    затем фильтрует standalone-токены AND/OR/NOT/NEAR.
    Возвращает очищенную строку для безопасной FTS5-матч-операции.
    """
    # Удаляем FTS5-спецсимволы: кавычки, скобки, операторы столбцов и т.д.
    for char in ('"', "'", "(", ")", ":", "^", "*"):
        text = text.replace(char, " ")
    # Удаляем standalone FTS5-операторы (AND, OR, NOT, NEAR)
    # — матч только по этим токенам не имеет смысла и ломает синтаксис
    _FTS5_OPERATORS = frozenset({"AND", "OR", "NOT", "NEAR"})
    text = " ".join(w for w in text.split() if w.upper() not in _FTS5_OPERATORS)
    return text.strip()


def _fts_query_for(query: str) -> str:
    """Build an FTS5-safe MATCH expression from free-text user query.

    Каждое слово преобразуется в префиксное совпадение (word*).
    Русские слова дополнительно расширяются морфологическими вариантами
    (напр. «купил» → «купил» OR «купила» OR «покупал» OR «покупать»)
    для повышения recall'а FTS5 на ~30–40%%.

    Использует ``_escape_fts_query`` для защиты от FTS5 syntax injection
    через спецсимволы и операторные ключевые слова.
    """
    # Экранируем FTS5-спецсимволы и операторы перед построением запроса
    safe_query = _escape_fts_query(query)
    if not safe_query:
        return ""

    parts: list[str] = []
    for raw in safe_query.split():
        clean = "".join(ch for ch in raw if ch.isalnum() or ch in "_-")
        if len(clean) < 2:
            continue

        # Проверяем русскую морфологическую экспансию
        ru_expanded = _try_expand_russian_word(clean)
        if ru_expanded:
            parts.append(ru_expanded)
        else:
            # Стандартное префиксное совпадение для нерусских слов
            parts.append(clean.lower() + "*")

    if not parts:
        return ""
    return " OR ".join(parts)


async def fts_search(
    session: AsyncSession,
    user_id: int,
    query: str,
    *,
    limit: int = 50,
) -> list[FtsHit]:
    fts_q = _fts_query_for(query)
    if not fts_q:
        return []
    sql = """
        SELECT m.user_id, m.peer_id, m.message_id, m.sender_name,
               snippet(messages_fts, -1, '', '', '…', 16) AS snippet,
               bm25(messages_fts) AS rank,
               c.display_name AS peer_name,
               m.date
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        LEFT JOIN contacts c ON c.user_id = m.user_id AND c.peer_id = m.peer_id
        WHERE messages_fts MATCH :q AND m.user_id = :uid
        ORDER BY rank
        LIMIT :lim
    """
    result = await session.execute(
        sql_text(sql),
        {"q": fts_q, "uid": user_id, "lim": limit},
    )
    rows = result.mappings().all()
    return [
        FtsHit(
            user_id=int(r["user_id"]),
            peer_id=int(r["peer_id"]),
            message_id=int(r["message_id"]),
            sender_name=r["sender_name"],
            snippet=r["snippet"] or "",
            rank=float(r["rank"]) if r["rank"] is not None else 0.0,
            peer_name=r["peer_name"],
            date=r["date"],
        )
        for r in rows
    ]


async def cross_chat_search(
    session: AsyncSession,
    user,
    query: str,
    limit: int = 5,
    *,
    peer_id: int | None = None,
) -> list[dict]:
    """Cross-chat FTS5 search — searches ALL messages and returns top conversations.

    For each matching conversation returns:
      - peer_id, display_name (from Contact)
      - top 2-3 snippets with highlighted matches (via FTS5 snippet())
      - total matching messages count

    Results are ordered by total matches DESC.

    Args:
        session: DB session.
        user: Bot user.
        query: Free-text search query (each word becomes a prefix OR-match).
        limit: Max number of conversations to return.
        peer_id: Optional — scope search to a single peer/chat.

    Returns:
        List of dicts with keys:
          peer_id, display_name, total_matches, snippets
        Each snippet is a dict: {"sender_name": str | None, "text": str, "date": datetime | None}
    """
    fts_q = _fts_query_for(query)
    if not fts_q:
        return []

    # ── Step 1: find top peer_ids by match count ──────────────────────
    peer_filter = " AND m.peer_id = :pid" if peer_id is not None else ""
    count_sql = f"""
        SELECT m.peer_id, c.display_name, COUNT(*) AS total_matches
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        LEFT JOIN contacts c ON c.user_id = m.user_id AND c.peer_id = m.peer_id
        WHERE messages_fts MATCH :q AND m.user_id = :uid{peer_filter}
        GROUP BY m.peer_id
        ORDER BY total_matches DESC
        LIMIT :lim
    """
    count_params: dict[str, object] = {"q": fts_q, "uid": user.id, "lim": limit}
    if peer_id is not None:
        count_params["pid"] = peer_id

    result = await session.execute(sql_text(count_sql), count_params)
    rows = result.mappings().all()
    if not rows:
        return []

    peer_ids: list[int] = []
    peer_info: dict[int, tuple[str | None, int]] = {}
    for r in rows:
        pid = int(r["peer_id"])
        peer_ids.append(pid)
        peer_info[pid] = (r["display_name"], int(r["total_matches"]))

    # ── Step 2: fetch top-3 snippets per peer_id ────────────────────
    # Build a dynamic IN clause for the selected peer_ids
    placeholders = ", ".join(f":pid_{i}" for i in range(len(peer_ids)))
    params: dict[str, object] = {"q": fts_q, "uid": user.id}
    for i, pid in enumerate(peer_ids):
        params[f"pid_{i}"] = pid

    peer_filter_snippet = " AND m.peer_id = :pid" if peer_id is not None else ""
    snippet_sql = f"""
        SELECT m.peer_id, m.sender_name, m.date,
               snippet(messages_fts, -1, '<b>', '</b>', '…', 64) AS snippet
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        WHERE messages_fts MATCH :q AND m.user_id = :uid{peer_filter_snippet}
          AND m.peer_id IN ({placeholders})
        ORDER BY m.peer_id, bm25(messages_fts)
    """
    if peer_id is not None:
        params["pid"] = peer_id

    result = await session.execute(sql_text(snippet_sql), params)
    snippet_rows = result.mappings().all()

    snippets_by_peer: dict[int, list[dict]] = {}
    for r in snippet_rows:
        pid = int(r["peer_id"])
        if pid not in snippets_by_peer:
            snippets_by_peer[pid] = []
        if len(snippets_by_peer[pid]) < 3:
            snippets_by_peer[pid].append(
                {
                    "sender_name": r["sender_name"],
                    "text": r["snippet"] or "",
                    "date": r["date"],
                }
            )

    # ── Step 3: build result preserving peer order ──────────────────
    output: list[dict] = []
    for pid in peer_ids:
        display_name, total = peer_info[pid]
        output.append(
            {
                "peer_id": pid,
                "display_name": display_name,
                "total_matches": total,
                "snippets": snippets_by_peer.get(pid, []),
            }
        )

    return output


async def add_memory(
    session: AsyncSession,
    user,
    *,
    fact: str,
    contact_id: int | None = None,
    sentiment: str | None = None,
    source: str = "chat",
    confidence: float = 0.5,
    message_id: int | None = None,
    cluster_topic: str | None = None,
    deduplicate: bool = True,
    embedding: list[float] | None = None,
    vector_store_obj: "VectorStore | None" = None,
    importance: float | None = None,
    decay_rate: float | None = None,
    memory_tier: int = 1,
    memory_type: str | None = None,
    pinned: bool = False,
    expires_at: datetime | None = None,
    use_count: int = 0,
    source_quality: float = 0.5,
    extraction_quality: float = 0.5,
) -> Memory | None:
    """
    Добавляет факт в память с дедупликацией.

    Два уровня дедупликации (при deduplicate=True):
      1. SHA256 хеш — точные повторы.
      2. Если передан embedding + vector_store_obj — семантическая
         дедупликация через Qdrant с динамическим порогом:
           - 0.92 — тот же source, возраст <7 дней (строже)
           - 0.78 — разные source (мягче)
           - 0.85 — остальные случаи

    При обнаружении дубликата повышает confidence (вес от source)
    и times_mentioned. Если факт содержит временные маркеры
    ("сейчас", "раньше", "уже не", "больше не", "перестал") —
    всегда создаётся новая запись.
    Если embedding передан, индексирует факт в Qdrant для будущих проверок.
    """
    from src.core.actions.stats_cache import invalidate
    from src.db.repos.session_repo import _get_user_lock

    fact = fact.strip()
    if len(fact) < 3:
        return None

    # Хеш для дедупликации (первые 64 бита SHA256)
    emb_hash = hashlib.sha256(fact.lower().strip().encode()).hexdigest()[:16]

    # Вес source для повышения confidence при мерже
    source_weight = {"chat": 0.3, "user": 0.6, "weekly": 0.15}.get(source, 0.3)

    # Временные маркеры — не мерджим, создаём как новый факт
    # M5: намеренно пропускаем ВСЮ дедупликацию (и SHA256, и Qdrant) для фактов
    # с временными маркерами. Даже если факт идентичен предыдущему, временной
    # маркер («сейчас», «уже не», «перестал») сигнализирует об ИЗМЕНЕНИИ
    # состояния — нужно создать новую запись, а не мерджить со старой.
    # Торговля (tradeoff): возможны дубликаты («сейчас я работаю в X» →
    # «сейчас я работаю в X» через день), но ложно-отрицательный пропуск
    # изменения («перестал работать в Y») критичнее чем дубликат.
    temporal_markers = {"сейчас", "раньше", "уже не", "больше не", "перестал"}
    has_temporal_marker = any(m in fact.lower() for m in temporal_markers)

    # Per-user lock to prevent concurrent dedup+insert race
    # NOTE: Per-user lock serializes execution but does not prevent
    # cross-transaction duplicates. DB-level UNIQUE constraint handles
    # this for SQLite.
    lock = _get_user_lock(user.id)

    async with lock:
        if deduplicate and not has_temporal_marker:
            # --- Уровень 1: SHA256 хеш (точные повторы) ---
            result = await session.execute(
                select(Memory)
                .where(
                    Memory.user_id == user.id,
                    Memory.embedding_hash == emb_hash,
                )
                .limit(1)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.times_mentioned = (existing.times_mentioned or 1) + 1
                existing.confidence = min(1.0, existing.confidence + source_weight)
                # Meta-Memory: corroboration — факт подтверждён повторно
                existing.corroboration_count = (existing.corroboration_count or 0) + 1
                existing.last_corroborated_at = datetime.now(timezone.utc)
                existing.updated_at = datetime.now(timezone.utc)
                if sentiment and existing.sentiment != sentiment:
                    existing.sentiment = "contradictory"  # маркируем противоречие
                await session.flush()
                await invalidate("mem_")
                from src.core.memory.memory_recall import bump_recall_version

                await bump_recall_version(user.telegram_id)
                return existing

            # --- Уровень 2: семантическая дедупликация через Qdrant ---
            if embedding is not None and vector_store_obj is not None:
                # Проверяем кэш эмбеддингов (на случай если embed уже закэширован)

                # Ищем кандидатов с запасом (порог 0.7)
                similar = await vector_store_obj.search_similar_memories(
                    user_id=user.id,
                    embedding=embedding,
                    threshold=0.7,
                    limit=3,
                )
                if similar:
                    best = similar[0]
                    existing = await session.get(Memory, best["memory_id"])
                    if existing and existing.user_id == user.id:
                        # Динамический порог
                        now = datetime.now(timezone.utc)
                        age_days = (
                            (now - existing.created_at).days
                            if existing.created_at
                            else 999
                        )
                        same_source = existing.source == source
                        if same_source and age_days < 7:
                            dyn_threshold = 0.92
                        elif not same_source:
                            dyn_threshold = 0.78
                        else:
                            dyn_threshold = 0.85

                        if best["score"] >= dyn_threshold:
                            existing.times_mentioned = (
                                existing.times_mentioned or 1
                            ) + 1
                            existing.confidence = min(
                                1.0, existing.confidence + source_weight
                            )
                            # Meta-Memory: corroboration — факт подтверждён повторно
                            existing.corroboration_count = (
                                existing.corroboration_count or 0
                            ) + 1
                            existing.last_corroborated_at = now
                            existing.updated_at = now
                            if sentiment and existing.sentiment != sentiment:
                                existing.sentiment = "contradictory"
                            await session.flush()
                            await invalidate("mem_")
                            from src.core.memory.memory_recall import (
                                bump_recall_version,
                            )

                            await bump_recall_version(user.telegram_id)
                            return existing

        # Create new memory inside the lock — atomic with the dedup check
        mem = Memory(
            user_id=user.id,
            contact_id=contact_id,
            fact=fact,
            sentiment=sentiment,
            source=source,
            confidence=confidence,
            times_mentioned=1,
            message_id=message_id,
            is_active=True,
            cluster_topic=cluster_topic,
            embedding_hash=emb_hash,
            importance=importance if importance is not None else 0.5,
            decay_rate=decay_rate if decay_rate is not None else 0.07,
            memory_tier=memory_tier,
            memory_type=memory_type,
            pinned=pinned,
            expires_at=expires_at,
            use_count=use_count,
            source_quality=source_quality,
            extraction_quality=extraction_quality,
        )
        session.add(mem)
        await session.flush()

    # Auto-link: connect to related facts via cosine similarity (Qdrant) or keyword overlap fallback
    await _auto_link_memory(session, user, mem, embedding=embedding)

    try:
        from src.core.infra.hooks import hooks

        # Look up contact_name for hook callback
        contact_name: str | None = None
        if contact_id is not None:
            try:
                contact_result = await session.execute(
                    select(Contact.display_name).where(
                        Contact.user_id == user.id,
                        Contact.peer_id == contact_id,
                    )
                )
                contact_name = contact_result.scalar_one_or_none()
            except SQLAlchemyError:
                contact_name = None

        await hooks.emit(
            "on_memory_saved",
            memory_id=mem.id,
            fact=fact,
            user_id=user.telegram_id,
            contact_id=contact_id,
            contact_name=contact_name,
            confidence=confidence,
        )
    except Exception:  # ACTION: narrow exception class — hooks.emit может поднять любые исключения от плагинов.
        # Безопасно игнорируем — хуки опциональны.
        pass  # hooks are optional, never break core flow

    # Индексируем эмбеддинг в Qdrant для будущей дедупликации
    if embedding is not None and vector_store_obj is not None:
        try:
            await vector_store_obj.upsert_memory(
                memory_id=mem.id,
                user_id=user.id,
                contact_id=contact_id,
                fact=fact,
                embedding=embedding,
            )
        except (
            Exception
        ):  # ACTION: narrow to Qdrant-specific exception — сетевой вызов Qdrant.
            # При падении Qdrant продолжаем без векторного индекса.
            # M4: факт сохранён в SQLite, но НЕ в Qdrant — дедупликация и поиск
            # по вектору не увидят этот факт. Логируем ERROR с memory_id чтобы
            # мониторинг мог отследить рассинхрон и запустить переиндексацию.
            logger.error(
                "CRITICAL: Failed to index memory %d in Qdrant — "
                "fact saved in SQLite but NOT searchable via vector. "
                "Re-index required: memory_id=%d user_id=%d",
                mem.id,
                mem.id,
                user.id,
                exc_info=True,
            )

    await invalidate("mem_")
    from src.core.memory.memory_recall import bump_recall_version

    await bump_recall_version(user.telegram_id)
    if contact_id is not None:
        await invalidate_contact_digest(contact_id)
    return mem


async def _batch_link_memories(
    session: AsyncSession,
    user,
    pending_links: list[tuple[int, int, float, str | None]],
) -> int:
    """Batch-create/update MemoryLinks. Returns count of links created/updated.

    # Batch query optimization — replaces N individual link_memories() calls
    # with 3 queries: ownership check, existing check, batch insert.
    """
    if not pending_links:
        return 0

    # De-duplicate by (source, target) pairs
    seen: set[tuple[int, int]] = set()
    unique_links: list[tuple[int, int, float, str | None]] = []
    for src, tgt, w, rt in pending_links:
        key = (src, tgt)
        if key not in seen:
            seen.add(key)
            unique_links.append((src, tgt, w, rt))

    if not unique_links:
        return 0

    # Collect all memory IDs involved
    all_ids: set[int] = set()
    for src, tgt, _, _ in unique_links:
        all_ids.add(src)
        all_ids.add(tgt)

    # Batch verify ownership — 1 query instead of N
    valid_ids = set(
        (
            await session.execute(
                select(Memory.id).where(
                    Memory.id.in_(list(all_ids)), Memory.user_id == user.id
                )
            )
        )
        .scalars()
        .all()
    )

    if not valid_ids:
        return 0

    # Filter to only valid pairs (both source and target must belong to user)
    valid_links = [
        (src, tgt, w, rt)
        for src, tgt, w, rt in unique_links
        if src in valid_ids and tgt in valid_ids
    ]
    if not valid_links:
        return 0

    # Batch check existing links — 1 query instead of 2*N
    src_ids = [l[0] for l in valid_links]
    tgt_ids = [l[1] for l in valid_links]
    existing_result = await session.execute(
        select(MemoryLink.source_id, MemoryLink.target_id).where(
            MemoryLink.user_id == user.id,
            MemoryLink.source_id.in_(src_ids),
            MemoryLink.target_id.in_(tgt_ids),
        )
    )
    existing_pairs: set[tuple[int, int]] = {(r[0], r[1]) for r in existing_result.all()}

    # Build links to create/update
    count = 0
    updates_to_apply: list[tuple[int, int, float, str | None]] = []
    for src, tgt, w, rt in valid_links:
        if (src, tgt) in existing_pairs:
            updates_to_apply.append((src, tgt, w, rt))
        else:
            # Create new forward link
            link = MemoryLink(
                user_id=user.id,
                source_id=src,
                target_id=tgt,
                weight=w,
                relation_type=rt,
            )
            session.add(link)

            # Create reverse link if not exists
            if (tgt, src) not in existing_pairs:
                rev = MemoryLink(
                    user_id=user.id,
                    source_id=tgt,
                    target_id=src,
                    weight=w,
                    relation_type=rt,
                )
                session.add(rev)
                existing_pairs.add(
                    (tgt, src)
                )  # prevent duplicate if (B,A) in pending_links
                count += 1

    if updates_to_apply:
        weight_cases = case(
            *[
                (
                    (MemoryLink.source_id == src) & (MemoryLink.target_id == tgt),
                    w,
                )
                for src, tgt, w, rt in updates_to_apply
            ],
            else_=MemoryLink.weight,
        )

        rt_cases = case(
            *[
                (
                    (MemoryLink.source_id == src) & (MemoryLink.target_id == tgt),
                    rt if rt else MemoryLink.relation_type,
                )
                for src, tgt, w, rt in updates_to_apply
            ],
            else_=MemoryLink.relation_type,
        )

        await session.execute(
            update(MemoryLink)
            .where(
                MemoryLink.user_id == user.id,
                or_(
                    *[
                        (MemoryLink.source_id == src) & (MemoryLink.target_id == tgt)
                        for src, tgt, w, rt in updates_to_apply
                    ]
                ),
            )
            .values(weight=weight_cases, relation_type=rt_cases)
        )

    if count or updates_to_apply:
        await session.flush()

    return count


async def _auto_link_memory(
    session: AsyncSession, user, memory, embedding: list[float] | None = None
) -> None:
    """Auto-link new fact to related facts via multiple strategies.

    Primary: Qdrant cosine similarity → "supports" / "related".
    Fallback: keyword overlap → "related".
    Supplementary passes (always run):
      - Temporal co-occurrence (same contact, <1h apart) → "co_temporal"
      - Entity co-occurrence (shared proper nouns) → "co_entity"
      - Cause-effect hint (positive→negative same contact) → "preceded"

    All supplementary passes add links on top of the existing ones.

    # Batch query optimization — collects all links and flushes in batch.
    """
    # Lazy import to avoid circular: memory_repo -> relation_types -> core.memory
    from src.core.memory.relation_types import RelationType

    if not memory.fact or not memory.is_active:
        return

    # Collect all pending links, flush once at the end
    pending_links: list[tuple[int, int, float, str | None]] = []

    # ── Pass 1: Semantic linking via Qdrant ──────────────────────────────
    if embedding:
        try:
            from src.core.actions.vector_store import get_vector_store

            similar = await (await get_vector_store()).search_similar_memories(
                user_id=user.id,
                embedding=embedding,
                threshold=0.65,  # lower than dedup (0.85)
                limit=10,
                contact_id=None,  # search across all contacts for cross-links
            )

            for hit in similar:
                hit_id = hit.get("memory_id")
                if hit_id is None or hit_id == memory.id:
                    continue

                cosine_score = hit.get("score", 0.0)
                if cosine_score < 0.65:
                    continue

                if cosine_score >= 0.90:
                    relation_type = RelationType.SUPPORTS
                elif cosine_score >= 0.75:
                    relation_type = RelationType.RELATED  # strong
                else:
                    relation_type = RelationType.RELATED  # weak

                pending_links.append((memory.id, hit_id, cosine_score, relation_type))
        except (
            Exception
        ):  # ACTION: narrow to Qdrant-specific exception — сетевой вызов Qdrant.
            # При ошибке fallback на keyword overlap.
            logger.debug(
                "Semantic linking failed, falling back to keyword overlap",
                exc_info=True,
            )

    # ── Pass 2: Keyword overlap fallback (only if no semantic links) ─────
    if len(pending_links) == 0:
        words = {w.lower() for w in memory.fact.split() if len(w) >= 4}
        if len(words) >= 2:
            candidates_q = (
                select(Memory)
                .where(
                    Memory.user_id == user.id,
                    Memory.is_active.is_(True),
                    Memory.id != memory.id,
                    Memory.contact_id == memory.contact_id,
                )
                .limit(30)
            )
            result = await session.execute(candidates_q)
            candidates = result.scalars().all()

            for c in candidates:
                if not c.fact:
                    continue
                c_words = {w.lower() for w in c.fact.split() if len(w) >= 4}
                overlap = len(words & c_words)
                if overlap >= 2:
                    weight = 0.3 + overlap * 0.1
                    pending_links.append(
                        (memory.id, c.id, weight, RelationType.RELATED)
                    )

    # ── Pass 3: Temporal co-occurrence ───────────────────────────────────
    # Same contact, created_at within 1 hour
    if memory.contact_id is not None and memory.created_at is not None:
        window_start = memory.created_at - timedelta(hours=1)
        window_end = memory.created_at + timedelta(hours=1)
        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == user.id,
                Memory.is_active.is_(True),
                Memory.id != memory.id,
                Memory.contact_id == memory.contact_id,
                Memory.created_at.between(window_start, window_end),
            )
            .limit(30)
        )
        for c in result.scalars().all():
            if not c.fact:
                continue
            pending_links.append((memory.id, c.id, 0.5, RelationType.CO_TEMPORAL))

    # ── Pass 4: Entity co-occurrence (shared proper nouns) ───────────────
    # Simple: capitalized word >= 3 chars
    proper_nouns: set[str] = set()
    for word in memory.fact.split():
        clean = word.strip(".,!?;:'\"()[]{}")
        if len(clean) >= 3 and clean[0].isupper() and clean.isalpha():
            proper_nouns.add(clean)
    if proper_nouns:
        conditions = [Memory.fact.ilike(f"%{pn}%") for pn in proper_nouns]
        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == user.id,
                Memory.is_active.is_(True),
                Memory.id != memory.id,
                or_(*conditions),
            )
            .limit(30)
        )
        for c in result.scalars().all():
            if not c.fact:
                continue
            # Double-check: does c.fact actually contain any of the same proper nouns?
            c_upper = {
                w.strip(".,!?;:'\"()[]{}")
                for w in c.fact.split()
                if len(w.strip(".,!?;:'\"()[]{}")) >= 3
                and w.strip(".,!?;:'\"()[]{}")[0].isupper()
                and w.strip(".,!?;:'\"()[]{}").isalpha()
            }
            if proper_nouns & c_upper:
                pending_links.append((memory.id, c.id, 0.4, RelationType.CO_ENTITY))

    # ── Pass 5: Cause-effect hint ────────────────────────────────────────
    # If new fact is negative, link from older positive facts of same contact
    if (
        memory.sentiment == "negative"
        and memory.contact_id is not None
        and memory.created_at is not None
    ):
        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == user.id,
                Memory.is_active.is_(True),
                Memory.id != memory.id,
                Memory.contact_id == memory.contact_id,
                Memory.sentiment == "positive",
                Memory.created_at < memory.created_at,
            )
            .limit(10)
        )
        for c in result.scalars().all():
            if not c.fact:
                continue
            pending_links.append((c.id, memory.id, 0.3, RelationType.PRECEDED))

    # ── Batch flush all links ────────────────────────────────────────────
    if pending_links:
        links_added = await _batch_link_memories(session, user, pending_links)
        if links_added:
            logger.debug(
                "Auto-linked %d facts to memory %d",
                links_added,
                memory.id,
            )


async def list_memories(
    session: AsyncSession,
    user,
    *,
    contact_id: int | None = None,
    limit: int | None = None,
    is_active: bool | None = None,
    has_tags: bool | None = None,
) -> list[Memory]:
    query = (
        select(Memory)
        .where(Memory.user_id == user.id)
        .order_by(Memory.created_at.desc())
    )
    if contact_id is not None:
        query = query.where(Memory.contact_id == contact_id)
    if is_active is not None:
        query = query.where(Memory.is_active == is_active)
    if has_tags is not None:
        if has_tags:
            query = query.where(Memory.tags.isnot(None), Memory.tags != "")
        else:
            from sqlalchemy import or_

            query = query.where(or_(Memory.tags.is_(None), Memory.tags == ""))
    if limit is not None:
        query = query.limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def delete_memory(session: AsyncSession, user, memory_id: int) -> bool:
    from src.core.actions.stats_cache import invalidate

    m = await session.get(Memory, memory_id)
    if m is None or m.user_id != user.id:
        return False
    # Soft delete — данные не удаляются безвозвратно
    m.is_active = False
    m.validity_end = datetime.now(timezone.utc)
    await invalidate("mem_")
    await session.flush()
    from src.core.memory.memory_recall import bump_recall_version

    await bump_recall_version(user.telegram_id)
    if m.contact_id is not None:
        await invalidate_contact_digest(m.contact_id)
    return True


async def add_memory_candidate(
    session: AsyncSession,
    user,
    *,
    fact: str,
    contact_id: int | None = None,
    sentiment: str | None = None,
    memory_type: str | None = None,
    source: str = "chat",
    importance: float = 0.5,
    decay_rate: float = 0.07,
) -> MemoryCandidate:
    candidate = MemoryCandidate(
        user_id=user.id,
        contact_id=contact_id,
        fact=fact,
        sentiment=sentiment,
        memory_type=memory_type,
        source=source,
        importance=importance,
        decay_rate=decay_rate,
    )
    session.add(candidate)
    await session.flush()
    return candidate


async def list_memory_candidates(
    session: AsyncSession,
    user,
    limit: int = 20,
) -> list[MemoryCandidate]:
    result = await session.execute(
        select(MemoryCandidate)
        .where(MemoryCandidate.user_id == user.id)
        .order_by(MemoryCandidate.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def delete_memory_candidate(
    session: AsyncSession,
    user,
    candidate_id: int,
) -> bool:
    obj = await session.get(MemoryCandidate, candidate_id)
    if obj and obj.user_id == user.id:
        await session.delete(obj)
        return True
    return False


async def search_memories(
    session: AsyncSession,
    user,
    query: str,
    *,
    contact_id: int | None = None,
) -> list[Memory]:
    # Пробуем FTS5 сначала; если пусто — ILIKE fallback
    results = await search_memories_fts(session, user, query, contact_id=contact_id)
    if results:
        return results
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == user.id,
            Memory.fact.icontains(query),
        )
        .order_by(Memory.created_at.desc())
    )
    if contact_id is not None:
        stmt = stmt.where(Memory.contact_id == contact_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def search_memories_fts(
    session: AsyncSession,
    user,
    query: str,
    *,
    contact_id: int | None = None,
    limit: int = 50,
) -> list[Memory]:
    """Полнотекстовый поиск по памяти через FTS5 с ранжированием по bm25().

    Использует _fts_query_for() для преобразования запроса в FTS5-safe формат.
    """
    fts_q = _fts_query_for(query)
    if not fts_q:
        return []

    base_sql = """
        SELECT m.id FROM memories_fts
        JOIN memories m ON m.id = memories_fts.rowid
        WHERE memories_fts MATCH :q AND m.user_id = :uid
    """
    if contact_id is not None:
        base_sql += " AND m.contact_id = :cid"
    base_sql += " ORDER BY bm25(memories_fts) LIMIT :lim"

    params = {"q": fts_q, "uid": user.id, "lim": limit}
    if contact_id is not None:
        params["cid"] = contact_id

    result = await session.execute(sql_text(base_sql), params)
    ids = [r[0] for r in result.fetchall()]
    if not ids:
        return []

    rows = await session.execute(select(Memory).where(Memory.id.in_(ids)))
    mem_map = {m.id: m for m in rows.scalars().all()}
    return [mem_map[mid] for mid in ids if mid in mem_map]


async def search_memories_fts_with_scores(
    session: AsyncSession,
    user,
    query: str,
    *,
    contact_id: int | None = None,
    limit: int = 20,
) -> list[tuple[int, float]]:
    """FTS5 keyword search on memories_fts returning (memory_id, bm25_score).

    Returns results sorted by BM25 rank (ascending — lower is better).
    This is the keyword counterpart to vector_store.search_similar_memories()
    for use in reciprocal rank fusion (RRF).
    """
    fts_query = _fts_query_for(query)
    if not fts_query:
        return []

    sql_parts = [
        "SELECT m.id, bm25(memories_fts) AS score",
        "FROM memories_fts",
        "JOIN memories m ON m.id = memories_fts.rowid",
        "WHERE memories_fts MATCH :q AND m.user_id = :uid",
    ]
    params: dict = {"q": fts_query, "uid": user.id}

    if contact_id is not None:
        sql_parts.append("AND m.contact_id = :cid")
        params["cid"] = contact_id

    sql_parts.append("ORDER BY score")
    sql_parts.append("LIMIT :lim")
    params["lim"] = limit

    sql = "\n".join(sql_parts)
    result = await session.execute(sql_text(sql), params)
    rows = result.all()

    # Return (memory_id, bm25_score) — lower BM25 = better match
    return [(int(r[0]), float(r[1])) for r in rows if r[1] is not None]


async def find_similar_memories(
    session: AsyncSession, user, fact: str, threshold: float = 0.7
) -> list[Memory]:
    """Поиск похожих фактов: пробуем FTS5, fallback на ILIKE."""
    from sqlalchemy import text as _sa_text

    # Try FTS5 first
    try:
        fts_terms = [
            w + "*"
            for w in fact.split()
            if len(w) > 1 and w.replace("_", "").replace("-", "").isalnum()
        ]
        if fts_terms:
            fts_q = " OR ".join(fts_terms)
            result = await session.execute(
                select(Memory)
                .where(
                    Memory.user_id == user.id,
                    _sa_text("memories_fts MATCH :q").bindparams(q=fts_q),
                )
                .limit(20)
            )
            results = list(result.scalars().all())
            if results:
                return results
    except SQLAlchemyError:
        pass  # FTS5 table may not exist or query invalid, fall through to ILIKE

    # ILIKE fallback
    words = [w for w in fact.lower().split() if len(w) > 2]
    if not words:
        return []
    conditions = [Memory.fact.icontains(w) for w in words[:5]]
    result = await session.execute(
        select(Memory).where(Memory.user_id == user.id, or_(*conditions))
    )
    return list(result.scalars().all())


async def get_memory_stats(session: AsyncSession, user) -> dict:
    """Статистика по памяти (кэшируется на 5 минут). SQL-агрегация вместо загрузки всех объектов."""
    from src.core.actions.stats_cache import get_cached, set_cache

    cache_key = f"mem_stats:{user.id}"
    cached = await get_cached(cache_key)
    if cached is not None:
        return cached

    # Скалярные агрегаты одним запросом
    r = await session.execute(
        select(
            func.count().label("total"),
            func.coalesce(
                func.sum(case((Memory.confidence >= 0.8, 1), else_=0)), 0
            ).label("high_confidence"),
            func.coalesce(
                func.sum(case((Memory.contact_id.isnot(None), 1), else_=0)), 0
            ).label("with_contact"),
        ).where(Memory.user_id == user.id, Memory.is_active)
    )
    row = r.one()

    # По тональности
    sent_rows = (
        await session.execute(
            select(
                func.coalesce(Memory.sentiment, "neutral").label("sentiment"),
                func.count().label("cnt"),
            )
            .where(Memory.user_id == user.id, Memory.is_active)
            .group_by(func.coalesce(Memory.sentiment, "neutral"))
        )
    ).all()
    by_sentiment = {sr.sentiment: sr.cnt for sr in sent_rows}

    # По источникам
    src_rows = (
        await session.execute(
            select(Memory.source, func.count().label("cnt"))
            .where(Memory.user_id == user.id, Memory.is_active)
            .group_by(Memory.source)
        )
    ).all()
    by_source = {sr.source: sr.cnt for sr in src_rows}

    # По уровням памяти
    tier_rows = (
        await session.execute(
            select(
                Memory.memory_tier.label("tier"),
                func.count().label("cnt"),
            )
            .where(Memory.user_id == user.id, Memory.is_active)
            .group_by(Memory.memory_tier)
        )
    ).all()
    by_tier = {f"tier_{tr.tier}": tr.cnt for tr in tier_rows}

    stats = {
        "total": row.total,
        "by_sentiment": by_sentiment,
        "by_source": by_source,
        "by_tier": by_tier,
        "high_confidence": row.high_confidence,
        "with_contact": row.with_contact,
    }
    await set_cache(cache_key, stats)
    return stats


async def upsert_memory_cluster(
    session: AsyncSession,
    user,
    topic: str,
    *,
    summary: str | None = None,
    fact_count: int | None = None,
) -> MemoryCluster:
    """Создаёт или возвращает существующий кластер по теме."""
    result = await session.execute(
        select(MemoryCluster).where(
            MemoryCluster.user_id == user.id,
            MemoryCluster.topic == topic.lower().strip(),
        )
    )
    cluster = result.scalar_one_or_none()
    if cluster is None:
        cluster = MemoryCluster(user_id=user.id, topic=topic.lower().strip())
        session.add(cluster)
    if summary is not None:
        cluster.summary = summary
    if fact_count is not None:
        cluster.fact_count = fact_count
    await session.flush()
    return cluster


async def list_memory_clusters(session: AsyncSession, user) -> list[MemoryCluster]:
    """Список кластеров памяти."""
    result = await session.execute(
        select(MemoryCluster)
        .where(MemoryCluster.user_id == user.id)
        .order_by(MemoryCluster.fact_count.desc())
    )
    return list(result.scalars().all())


async def add_member(
    session: AsyncSession,
    user_id: int,
    memory_id: int,
    cluster_id: int,
    score: float = 0.5,
) -> None:
    """Добавляет факт в кластер."""
    m = MemoryClusterMember(
        user_id=user_id,
        memory_id=memory_id,
        cluster_id=cluster_id,
        relevance_score=score,
    )
    session.add(m)
    await session.flush()


async def get_cluster_members(
    session: AsyncSession,
    user,
    cluster_id: int,
    limit: int = 20,
) -> list[Memory]:
    """Факты кластера, отсортированы по relevance_score."""
    q = (
        select(Memory)
        .join(MemoryClusterMember, Memory.id == MemoryClusterMember.memory_id)
        .where(
            MemoryClusterMember.cluster_id == cluster_id,
            MemoryClusterMember.user_id == user.id,
            Memory.is_active,
        )
        .order_by(MemoryClusterMember.relevance_score.desc())
        .limit(limit)
    )
    r = await session.execute(q)
    return list(r.scalars().all())


async def list_clusters_for_contact(
    session: AsyncSession,
    user,
    contact_id: int | None = None,
) -> list:
    """Кластеры для контакта (или общие)."""
    q = (
        select(
            MemoryCluster,
            func.count(distinct(MemoryClusterMember.memory_id)).label("fact_count"),
        )
        .join(
            MemoryClusterMember,
            MemoryCluster.id == MemoryClusterMember.cluster_id,
        )
        .join(Memory, Memory.id == MemoryClusterMember.memory_id)
        .where(
            MemoryCluster.user_id == user.id,
            Memory.is_active,
        )
    )
    if contact_id is not None:
        q = q.where(Memory.contact_id == contact_id)
    q = (
        q.group_by(MemoryCluster.id)
        .order_by(func.count(distinct(MemoryClusterMember.memory_id)).desc())
        .limit(10)
    )
    r = await session.execute(q)
    return list(r.all())


async def link_memories(
    session: AsyncSession,
    user,
    source_id: int,
    target_id: int,
    weight: float = 0.5,
    relation_type: str | None = None,
) -> MemoryLink | None:
    """Создать/обновить связь между фактами памяти (many-to-many)."""

    # Валидация relation_type: LLM может галлюцинировать значения вроде
    # «supersede» (без 's') или «replaces». Такие значения молча попадут в БД
    # и не будут найдены ни одним relation-фильтром. Приводим к None вместо
    # райза — вызывающий код не должен падать из-за LLM-ошибки.
    if relation_type is not None and relation_type not in _VALID_RELATION_TYPES:
        logger.warning(
            "Invalid relation_type=%r for link %d->%d, dropping relation",
            relation_type,
            source_id,
            target_id,
        )
        relation_type = None

    # Проверить что оба факта принадлежат пользователю
    result = await session.execute(
        select(Memory).where(
            Memory.id.in_([source_id, target_id]), Memory.user_id == user.id
        )
    )
    if len(result.scalars().all()) < 2:
        return None  # один из фактов не найден или чужой

    # Проверить существующую связь
    existing = await session.execute(
        select(MemoryLink).where(
            MemoryLink.user_id == user.id,
            MemoryLink.source_id == source_id,
            MemoryLink.target_id == target_id,
        )
    )
    existing = existing.scalar_one_or_none()
    if existing:
        existing.weight = weight
        if relation_type:
            existing.relation_type = relation_type
        await session.flush()
        return existing

    # Создать новую + обратную
    link = MemoryLink(
        user_id=user.id,
        source_id=source_id,
        target_id=target_id,
        weight=weight,
        relation_type=relation_type,
    )
    session.add(link)

    # Обратная связь (если не дубль)
    # M7: обратная связь использует тот же relation_type что и прямая.
    # Семантически это неверно (relation_type описывает направление:
    # A supersedes B ≠ B supersedes A), но большинство downstream-кода
    # (memory_graph, memory_chain) работает с bidirectional edges и не
    # различает направление. Полноценный фикс потребовал бы reverse-
    # relation_type mapping (supersedes→superseded_by, cause→effect),
    # что — breaking change для memory_graph BFS.
    # Пока оставляем как есть — tradeoff осознанный.
    reverse_check = await session.execute(
        select(MemoryLink).where(
            MemoryLink.user_id == user.id,
            MemoryLink.source_id == target_id,
            MemoryLink.target_id == source_id,
        )
    )
    if not reverse_check.scalar_one_or_none():
        rev = MemoryLink(
            user_id=user.id,
            source_id=target_id,
            target_id=source_id,
            weight=weight,
            relation_type=relation_type,
        )
        session.add(rev)

    await session.flush()
    return link


async def unlink_memories(
    session: AsyncSession, user, source_id: int, target_id: int
) -> None:
    """Удалить связь между фактами (в обе стороны)."""
    from sqlalchemy import and_, or_

    from sqlalchemy import delete as sa_delete

    await session.execute(
        sa_delete(MemoryLink).where(
            MemoryLink.user_id == user.id,
            or_(
                and_(
                    MemoryLink.source_id == source_id,
                    MemoryLink.target_id == target_id,
                ),
                and_(
                    MemoryLink.source_id == target_id,
                    MemoryLink.target_id == source_id,
                ),
            ),
        )
    )
    await session.flush()


async def get_linked_memories(
    session: AsyncSession, user, memory_id: int, limit: int = 10
) -> list[dict]:
    """Получить связанные факты с весами."""
    result = await session.execute(
        select(Memory, MemoryLink.weight, MemoryLink.relation_type)
        .join(MemoryLink, MemoryLink.target_id == Memory.id)
        .where(
            MemoryLink.source_id == memory_id,
            MemoryLink.user_id == user.id,
            Memory.is_active,
        )
        .order_by(MemoryLink.weight.desc())
        .limit(limit)
    )
    rows = result.all()
    linked: list[dict] = []
    for mem, weight, rel_type in rows:
        linked.append({"memory": mem, "weight": weight, "relation_type": rel_type})
    return linked


async def get_memory_graph(
    session: AsyncSession,
    user,
    memory_id: int,
    max_depth: int = 3,
    max_nodes: int = 20,
) -> list[dict]:
    """Строит граф связанных фактов BFS от memory_id.

    Оптимизация: вместо N запросов (на каждый узел) делаем 2 запроса:
    1) все MemoryLink пользователя → строим adjacency dict
    2) все Memory для посещённых ID → batch load
    """
    # ── Phase 1: Load ALL MemoryLinks for this user in ONE query ──────
    rows = (
        await session.execute(
            select(
                MemoryLink.source_id,
                MemoryLink.target_id,
                MemoryLink.weight,
                MemoryLink.relation_type,
            )
            .where(MemoryLink.user_id == user.id)
            .order_by(MemoryLink.weight.desc())
            .limit(
                5000
            )  # HIGH: не загружать все 100K+ связей; BFS использует ≤20 узлов
        )
    ).all()

    # Build in-memory adjacency dict: source_id -> [(target_id, weight, rel_type), ...]
    # Already sorted by weight DESC from the DB query
    adj: dict[int, list[tuple[int, float, str | None]]] = {}
    for source_id, target_id, weight, relation_type in rows:
        adj.setdefault(source_id, []).append((target_id, weight, relation_type))

    # ── Phase 2: BFS walk using the in-memory adjacency dict ─────────
    visited: set[int] = set()
    graph: list[dict] = []
    queue: list[tuple[int, int]] = [(memory_id, 0)]
    while queue and len(visited) < max_nodes:
        mid, depth = queue.pop(0)
        if mid in visited or depth > max_depth:
            continue
        visited.add(mid)
        if depth > 0:  # не добавляем корневой узел в граф, только соседей
            # Memory будет загружен в Phase 3 (batch)
            graph.append({"memory_id": mid, "depth": depth})
        if depth < max_depth:
            # adj.get(mid, []) уже отсортирован по weight DESC из Phase 1
            for target_id, weight, rel_type in adj.get(mid, [])[:10]:
                if target_id not in visited:
                    queue.append((target_id, depth + 1))

    if not graph:
        return []

    # ── Phase 3: Load ALL needed Memory objects in ONE batch query ───
    mem_ids = {entry["memory_id"] for entry in graph}
    result = await session.execute(select(Memory).where(Memory.id.in_(mem_ids)))
    mem_lookup: dict[int, Memory] = {m.id: m for m in result.scalars().all()}

    # ── Phase 4: Assemble the graph from the lookup dict ──────────────
    for entry in graph:
        mid = entry.pop("memory_id")
        mem = mem_lookup.get(mid)
        if mem:
            entry["memory"] = mem
        # если memory удалена между Phase 2 и Phase 3 — пропускаем
        # (аналогично оригинальному поведению `if mem:`)

    return graph


async def get_graph_stats(session: AsyncSession, user_id: int) -> dict:
    """Return graph statistics: node count, edge type breakdown, top hubs, connected components, average degree.

    Uses SQL aggregation wherever possible; flood-fill in Python for connected components.
    """
    # ── 1. Node count (active memories) ──────────────────────────────
    node_count = (
        await session.execute(
            select(func.count())
            .select_from(Memory)
            .where(Memory.user_id == user_id, Memory.is_active)
        )
    ).scalar() or 0

    # ── 2. Edge counts by relation_type ─────────────────────────────
    edge_rows = (
        await session.execute(
            select(
                func.coalesce(MemoryLink.relation_type, "unknown").label("rel_type"),
                func.count().label("cnt"),
            )
            .where(MemoryLink.user_id == user_id)
            .group_by(func.coalesce(MemoryLink.relation_type, "unknown"))
        )
    ).all()
    edges_by_type: dict[str, int] = {r.rel_type: r.cnt for r in edge_rows}
    total_edges = sum(edges_by_type.values())

    # ── 3. Top-5 hub nodes (highest total degree: source + target) ──
    hub_sql = """
        SELECT node_id, SUM(degree) AS total_degree
        FROM (
            SELECT source_id AS node_id, COUNT(*) AS degree
            FROM memory_links
            WHERE user_id = :uid
            GROUP BY source_id
            UNION ALL
            SELECT target_id AS node_id, COUNT(*) AS degree
            FROM memory_links
            WHERE user_id = :uid
            GROUP BY target_id
        ) AS d
        GROUP BY node_id
        ORDER BY total_degree DESC
        LIMIT 5
    """
    hub_rows = (await session.execute(sql_text(hub_sql), {"uid": user_id})).all()

    top_hubs: list[dict] = []
    if hub_rows:
        hub_ids = [int(r[0]) for r in hub_rows]
        mems = (
            (await session.execute(select(Memory).where(Memory.id.in_(hub_ids))))
            .scalars()
            .all()
        )
        mem_map = {m.id: m for m in mems}
        for row in hub_rows:
            nid = int(row[0])
            mem = mem_map.get(nid)
            top_hubs.append(
                {
                    "memory_id": nid,
                    "degree": int(row[1]),
                    "fact": mem.fact[:80] if mem else "?",
                    "contact_id": mem.contact_id if mem else None,
                }
            )

    # ── 4. Connected components (flood fill) ─────────────────────────
    # Load all edges for flood-fill
    all_edges = (
        await session.execute(
            select(MemoryLink.source_id, MemoryLink.target_id)
            .where(MemoryLink.user_id == user_id)
            .distinct()
        )
    ).all()

    active_ids: set[int] = set(
        (
            await session.execute(
                select(Memory.id).where(Memory.user_id == user_id, Memory.is_active)
            )
        )
        .scalars()
        .all()
    )

    # Build undirected adjacency (only edges between active nodes)
    adj: dict[int, set[int]] = defaultdict(set)
    for s, t in all_edges:
        s_id, t_id = int(s), int(t)
        if s_id in active_ids and t_id in active_ids:
            adj[s_id].add(t_id)
            adj[t_id].add(s_id)

    # Nodes that appear in any edge
    nodes_in_edges: set[int] = set()
    for s, t in all_edges:
        s_id, t_id = int(s), int(t)
        nodes_in_edges.add(s_id)
        nodes_in_edges.add(t_id)

    # Isolated = active nodes with no edges
    isolated = len(active_ids - nodes_in_edges)

    # BFS only for connected nodes (non-isolated)
    connected_ids = active_ids & nodes_in_edges
    visited: set[int] = set()
    components = 0
    for nid in connected_ids:
        if nid in visited:
            continue
        components += 1
        queue = deque([nid])
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            for neighbor in adj.get(cur, set()):
                if neighbor not in visited:
                    queue.append(neighbor)

    # ── 5. Average degree ───────────────────────────────────────────
    avg_degree = round(total_edges / max(node_count, 1), 2)

    # ── 6. Isolated nodes (active nodes with no edges) ──────────────
    connected = len(visited)

    return {
        "node_count": node_count,
        "total_edges": total_edges,
        "edges_by_type": edges_by_type,
        "top_hubs": top_hubs,
        "components": components,
        "isolated_nodes": isolated,
        "avg_degree": avg_degree,
    }


# ── Impact Analysis ────────────────────────────────────────────────────


@dataclass
class ContactImpact:
    """Результат impact analysis для контакта."""

    contact_id: int
    contact_name: str
    direct_facts: list[Memory]
    related_contacts: list[dict]  # [{"id": int, "name": str, "via_fact": str}]
    topics: list[str]
    upcoming_events: list[dict]  # [{"text": str, "deadline": str}]
    total_nodes: int


async def contact_impact(
    session: AsyncSession,
    user_id: int,
    contact_id: int,
    max_depth: int = 2,
) -> ContactImpact:
    """Полный граф зависимостей контакта.

    Возвращает:
    - прямые факты о контакте
    - связанные контакты через MemoryLink
    - темы из кластеров
    - активные напоминания/дедлайны
    """
    # 1. Direct facts
    facts: list[Memory] = list(
        (
            await session.execute(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.contact_id == contact_id,
                    Memory.is_active == True,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )

    fact_ids = [f.id for f in facts]

    # 2. Related contacts via MemoryLink
    related_contacts: list[dict] = []
    if fact_ids:
        links_result = await session.execute(
            select(MemoryLink).where(
                MemoryLink.user_id == user_id,
                or_(
                    MemoryLink.source_id.in_(fact_ids),
                    MemoryLink.target_id.in_(fact_ids),
                ),
            )
        )
        links = links_result.scalars().all()

        neighbor_ids: set[int] = set()
        for link in links:
            other_id = link.target_id if link.source_id in fact_ids else link.source_id
            if other_id not in fact_ids:
                neighbor_ids.add(other_id)

        if neighbor_ids:
            neighbor_mems_result = await session.execute(
                select(Memory).where(
                    Memory.id.in_(list(neighbor_ids)),
                    Memory.is_active == True,  # noqa: E712
                    Memory.contact_id.isnot(None),
                    Memory.contact_id != contact_id,
                )
            )
            neighbor_mems = neighbor_mems_result.scalars().all()

            # Batch-load contact names to avoid N+1
            neighbor_cids: list[int] = []
            seen_contacts: set[int] = set()
            for nm in neighbor_mems:
                if nm.contact_id and nm.contact_id not in seen_contacts:
                    seen_contacts.add(nm.contact_id)
                    neighbor_cids.append(nm.contact_id)

            name_map: dict[int, str] = {}
            if neighbor_cids:
                names_result = await session.execute(
                    select(Contact.peer_id, Contact.display_name).where(
                        Contact.user_id == user_id,
                        Contact.peer_id.in_(neighbor_cids),
                    )
                )
                name_map = {
                    int(r[0]): r[1] or f"contact#{r[0]}" for r in names_result.all()
                }

            for nm in neighbor_mems:
                if nm.contact_id and nm.contact_id in seen_contacts:
                    cname = name_map.get(nm.contact_id, f"contact#{nm.contact_id}")
                    # Only add once per contact
                    if any(rc["id"] == nm.contact_id for rc in related_contacts):
                        continue
                    related_contacts.append(
                        {
                            "id": nm.contact_id,
                            "name": cname,
                            "via_fact": (nm.fact or "")[:60],
                        }
                    )

    # 3. Topics from clusters
    topics: list[str] = []
    if fact_ids:
        cluster_rows = (
            (
                await session.execute(
                    select(MemoryCluster.topic)
                    .join(
                        MemoryClusterMember,
                        MemoryClusterMember.cluster_id == MemoryCluster.id,
                    )
                    .where(
                        MemoryCluster.user_id == user_id,
                        MemoryClusterMember.memory_id.in_(fact_ids),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        topics = [t for t in cluster_rows if t]

    # 4. Upcoming commitments
    contact_name_row = await session.execute(
        select(Contact.display_name).where(
            Contact.user_id == user_id, Contact.peer_id == contact_id
        )
    )
    contact_name = contact_name_row.scalar() or f"contact#{contact_id}"

    events: list[dict] = []
    commitments_result = await session.execute(
        select(Commitment).where(
            Commitment.user_id == user_id,
            Commitment.status == "open",
        )
    )
    commitments = commitments_result.scalars().all()

    for c in commitments:
        if c.peer_name and contact_name and contact_name.lower() in c.peer_name.lower():
            events.append(
                {
                    "text": c.text or "",
                    "deadline": c.deadline_at.isoformat() if c.deadline_at else "",
                }
            )

    return ContactImpact(
        contact_id=contact_id,
        contact_name=contact_name,
        direct_facts=facts,
        related_contacts=related_contacts[:10],
        topics=topics[:10],
        upcoming_events=events[:5],
        total_nodes=len(facts) + len(related_contacts),
    )


# ── Memory Versioning / Audit Trail ──────────────────────────────────────


async def save_memory_version(
    session: AsyncSession,
    memory_id: int,
    fact_text: str,
    edited_by: str = "user",
    reason: str | None = None,
) -> MemoryVersion:
    """Сохранить версионный снимок факта памяти.

    Используется при каждом редактировании / деактивации / откате факта,
    чтобы сохранить полную историю изменений (audit trail).

    Args:
        session: Активная сессия БД.
        memory_id: ID факта памяти.
        fact_text: Текст факта на момент сохранения версии.
        edited_by: Кто внёс изменение ("user", "system", "agent").
        reason: Причина изменения (опционально).

    Returns:
        Созданный объект MemoryVersion.
    """
    # Получаем текущую максимальную версию для этого факта
    stmt = select(func.max(MemoryVersion.version)).where(
        MemoryVersion.memory_id == memory_id
    )
    result = await session.execute(stmt)
    max_ver: int = result.scalar() or 0

    version = MemoryVersion(
        memory_id=memory_id,
        version=max_ver + 1,
        fact_text=fact_text,
        edited_by=edited_by,
        reason=reason,
    )
    session.add(version)
    await session.flush()
    logger.debug(
        "Saved memory version v%d for memory_id=%d (edited_by=%s)",
        version.version,
        memory_id,
        edited_by,
    )
    return version


async def get_memory_history(
    session: AsyncSession,
    memory_id: int,
) -> list[MemoryVersion]:
    """Получить историю правок факта памяти (от новых к старым).

    Args:
        session: Активная сессия БД.
        memory_id: ID факта памяти.

    Returns:
        Список MemoryVersion, отсортированный по version DESC.
    """
    stmt = (
        select(MemoryVersion)
        .where(MemoryVersion.memory_id == memory_id)
        .order_by(MemoryVersion.version.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def rollback_memory(
    session: AsyncSession,
    memory_id: int,
    target_version: int,
) -> Memory | None:
    """Откатить факт памяти к указанной версии.

    Загружает текст из MemoryVersion с version == target_version,
    обновляет Memory.fact и сохраняет откат как новую версию
    (edited_by="system", reason="rollback to v{target_version}").

    Args:
        session: Активная сессия БД.
        memory_id: ID факта памяти.
        target_version: Номер версии, к которой нужно откатиться.

    Returns:
        Обновлённый объект Memory, или None если версия не найдена.
    """
    # Получаем целевую версию
    stmt = select(MemoryVersion).where(
        MemoryVersion.memory_id == memory_id,
        MemoryVersion.version == target_version,
    )
    ver = (await session.execute(stmt)).scalar_one_or_none()
    if not ver:
        logger.warning(
            "rollback_memory: version v%d not found for memory_id=%d",
            target_version,
            memory_id,
        )
        return None

    # Обновляем факт
    mem = await session.get(Memory, memory_id)
    if not mem:
        logger.warning("rollback_memory: memory_id=%d not found", memory_id)
        return None

    mem.fact = ver.fact_text
    mem.updated_at = datetime.now(timezone.utc)

    # Сохраняем откат как новую версию
    await save_memory_version(
        session,
        memory_id,
        ver.fact_text,
        edited_by="system",
        reason=f"rollback to v{target_version}",
    )

    await session.flush()
    logger.info("Rolled back memory_id=%d to v%d", memory_id, target_version)
    return mem
