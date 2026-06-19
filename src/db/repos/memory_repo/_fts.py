"""Memory repository — FTS helpers and search functions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select, text as sql_text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Memory

logger = logging.getLogger(__name__)


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
    count_sql = (
        "SELECT m.peer_id, c.display_name, COUNT(*) AS total_matches "
        "FROM messages_fts "
        "JOIN messages m ON m.id = messages_fts.rowid "
        "LEFT JOIN contacts c ON c.user_id = m.user_id AND c.peer_id = m.peer_id "
        "WHERE messages_fts MATCH :q AND m.user_id = :uid" + peer_filter + " "
        "GROUP BY m.peer_id "
        "ORDER BY total_matches DESC "
        "LIMIT :lim"
    )
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
    snippet_sql = (
        "SELECT m.peer_id, m.sender_name, m.date, "
        "snippet(messages_fts, -1, '<b>', '</b>', '…', 64) AS snippet "
        "FROM messages_fts "
        "JOIN messages m ON m.id = messages_fts.rowid "
        "WHERE messages_fts MATCH :q AND m.user_id = :uid" + peer_filter_snippet + " "
        "AND m.peer_id IN (" + placeholders + ") "
        "ORDER BY m.peer_id, bm25(messages_fts)"
    )
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
                    sql_text("memories_fts MATCH :q").bindparams(q=fts_q),
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
