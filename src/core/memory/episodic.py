"""P3: Episodic Memory — запись, поиск и рефлексия эпизодов/разговоров.

Эпизод сохраняет целостный контекст взаимодействия (кто, когда, тон, итог),
в отличие от Memory, которая хранит отдельные факты.

Ключевая фича: ночная рефлексия перечитывает старые эпизоды и извлекает
факты, которые smart_extractor пропустил при первом проходе.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models._memory import Episode, EpisodeContact
from src.db.models import Memory
from src.db.session import get_session

logger = logging.getLogger(__name__)

# ── Вспомогательные ──────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _extract_emotional_valence(text: str) -> float | None:
    """Простая оценка эмоционального тона по ключевым словам (MVP).

    Возвращает float от -1.0 (негатив) до 1.0 (позитив) или None при неопределённости.
    Для production можно заменить на LLM-вызов.
    """
    positive_words = (
        "отлично",
        "супер",
        "класс",
        "круто",
        "прекрасно",
        "замечательно",
        "рад",
        "счастлив",
        "люблю",
        "обожаю",
        "прекрасный",
        "хорошо",
        "потрясающе",
        "великолепно",
        "клёво",
        "здорово",
        "👍",
        "😊",
        "спасибо",
        "благодарю",
        "ура",
        "наконец-то",
        "получилось",
    )
    negative_words = (
        "ужасно",
        "плохо",
        "отвратительно",
        "бесит",
        "злюсь",
        "ненавижу",
        "грустно",
        "печально",
        "тоскливо",
        "разочарован",
        "обидно",
        "провал",
        "катастрофа",
        "пиздец",
        "хреново",
        "😢",
        "😡",
        "блин",
        "чёрт",
        "проблема",
        "не работает",
        "сломалось",
    )
    text_lower = text.lower()
    pos_count = sum(1 for w in positive_words if w in text_lower)
    neg_count = sum(1 for w in negative_words if w in text_lower)

    total = pos_count + neg_count
    if total == 0:
        return None  # нейтрально / не определено
    return round((pos_count - neg_count) / total, 3)


def _find_linked_memories(messages_text: str, memories: list[Memory]) -> list[int]:
    """Найти Memory-факты, которые упоминаются в тексте сообщений (строковый поиск).

    Простой подход: ищем keywords из фактов в тексте эпизода.
    Для MVP достаточно; можно заменить на векторный поиск.
    """
    linked: list[int] = []
    text_lower = messages_text.lower()
    for mem in memories:
        if not mem.is_active:
            continue
        # Ищем ключевые слова из факта (первые 3 слова)
        keywords = mem.fact.lower().split()[:5]
        if any(kw in text_lower for kw in keywords if len(kw) >= 4):
            linked.append(mem.id)
    return linked


# ── Core API ─────────────────────────────────────────────────────────────


async def create_episode(
    user_id: int,
    messages: list[str],
    contacts: list[dict] | None = None,
) -> Episode | None:
    """Создать эпизод из батча сообщений.

    Вызывается периодически (каждые N сообщений) в fire-and-forget режиме.

    Args:
        user_id: Telegram user ID владельца.
        messages: Список текстов сообщений (последние N).
        contacts: Список контактов [{id, name, role}, ...].

    Returns:
        Созданный Episode или None при ошибке.
    """
    if not messages:
        return None

    combined = "\n".join(messages)
    raw_sample = combined[:500] if len(combined) > 500 else combined
    valence = _extract_emotional_valence(combined)

    # Определяем важность: длинные/эмоциональные разговоры важнее
    importance = min(1.0, 0.3 + len(combined) / 5000 + (abs(valence or 0) * 0.3))

    # Ищем связанные Memory-факты
    memory_ids: list[int] = []
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.is_active == True,
                )
                .order_by(desc(Memory.importance))
                .limit(100)
            )
            memories = list(result.scalars().all())
            memory_ids = _find_linked_memories(combined, memories)
    except Exception:
        logger.debug("Linked memory search failed for episode", exc_info=True)

    # Пытаемся сделать лёгкое LLM-саммари (best-effort)
    summary = None
    if len(combined) > 100:
        try:
            summary = await _summarize_with_llm(user_id, messages)
        except Exception:
            logger.debug("Episode LLM summary failed, using raw", exc_info=True)

    try:
        async with get_session() as session:
            episode = Episode(
                user_id=user_id,
                started_at=_now_utc(),
                ended_at=_now_utc(),
                summary=summary,
                raw_sample=raw_sample,
                emotional_valence=valence,
                importance=round(importance, 3),
                memory_ids=json.dumps(memory_ids) if memory_ids else None,
            )
            session.add(episode)
            await session.flush()  # чтобы получить episode.id

            # Сохраняем контакты
            if contacts:
                for c in contacts:
                    ec = EpisodeContact(
                        episode_id=episode.id,
                        contact_id=c.get("id", 0),
                        contact_name=c.get("name"),
                        role=c.get("role", "participant"),
                    )
                    session.add(ec)

            logger.info(
                "Episode created: id=%d user=%d msgs=%d valence=%s importance=%.2f",
                episode.id,
                user_id,
                len(messages),
                valence,
                importance,
            )
            return episode
    except Exception:
        logger.exception("Failed to create episode for user %d", user_id)
        return None


async def _summarize_with_llm(user_id: int, messages: list[str]) -> str | None:
    """Лёгкий LLM-саммари эпизода (best-effort, не блокирует основной поток)."""
    if not messages:
        return None

    combined = "\n".join(messages)
    if len(combined) < 50:
        return combined[:200]

    try:
        from src.db.repo import get_or_create_user
        from src.llm.router import build_provider
        from src.llm.base import ChatMessage, TaskType

        async with get_session() as session:
            owner = await get_or_create_user(session, user_id)
            provider = await build_provider(
                session, owner, purpose="background", task_type=TaskType.SUMMARIZE
            )
            if not provider:
                return None

            prompt = (
                "Сделай краткое саммари этого разговора (2-3 предложения). "
                "Укажи: о чём говорили, кто участвовал, итог.\n\n"
                f"РАЗГОВОР:\n{combined[:2000]}\n\nСаммари:"
            )
            resp = await provider.chat([ChatMessage(role="user", content=prompt)])
            return resp[:500] if resp else None
    except Exception:
        logger.debug("LLM summary failed in episode creation", exc_info=True)
        return None


async def get_recent_episodes(
    user_id: int,
    limit: int = 10,
) -> list[Episode]:
    """Получить последние эпизоды пользователя."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Episode)
                .where(Episode.user_id == user_id)
                .order_by(desc(Episode.started_at))
                .limit(limit)
            )
            return list(result.scalars().all())
    except Exception:
        logger.exception("Failed to get recent episodes for user %d", user_id)
        return []


async def search_episodes(
    user_id: int,
    query: str,
    limit: int = 10,
) -> list[Episode]:
    """Поиск эпизодов по содержимому (строковый поиск — MVP).

    Ищет в summary и raw_sample. Для production можно заменить на FTS5.
    """
    if not query:
        return await get_recent_episodes(user_id, limit)

    query_lower = query.lower()
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Episode)
                .where(Episode.user_id == user_id)
                .order_by(desc(Episode.started_at))
                .limit(200)  # загружаем больше для фильтрации в Python
            )
            episodes = result.scalars().all()

            # Строковый поиск в Python (MVP)
            matched: list[Episode] = []
            for ep in episodes:
                text = f"{ep.summary or ''} {ep.raw_sample or ''}".lower()
                if query_lower in text:
                    matched.append(ep)
                if len(matched) >= limit:
                    break

            return matched
    except Exception:
        logger.exception("Failed to search episodes for user %d", user_id)
        return []


async def reflect_on_episodes(user_id: int) -> list[dict]:
    """Ночная рефлексия: перечитать старые эпизоды и извлечь новые факты.

    Это ключевая фича P3 — эпизоды содержат контекст, который
    smart_extractor мог пропустить при первом проходе.

    Returns:
        Список dict'ов с результатами:
        [{"episode_id": int, "new_facts": int, "facts": [str, ...]}, ...]
    """
    results: list[dict] = []
    try:
        async with get_session() as session:
            # Берём эпизоды без summary (сырые) за последние 7 дней
            cutoff = _now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta

            cutoff = cutoff - timedelta(days=7)

            result = await session.execute(
                select(Episode)
                .where(
                    Episode.user_id == user_id,
                    Episode.started_at >= cutoff,
                )
                .order_by(desc(Episode.importance))
                .limit(20)
            )
            episodes = result.scalars().all()

            if not episodes:
                logger.info("No episodes to reflect on for user %d", user_id)
                return results

            for ep in episodes:
                try:
                    new_facts = await _reflect_single_episode(user_id, ep, session)
                    if new_facts:
                        results.append(
                            {
                                "episode_id": ep.id,
                                "new_facts": len(new_facts),
                                "facts": new_facts,
                            }
                        )
                except Exception:
                    logger.debug(
                        "Reflection failed for episode %d", ep.id, exc_info=True
                    )

            logger.info(
                "Episode reflection done for user %d: %d episodes, %d new facts",
                user_id,
                len(episodes),
                sum(r["new_facts"] for r in results),
            )
    except Exception:
        logger.exception("Episode reflection failed for user %d", user_id)

    return results


async def _reflect_single_episode(
    user_id: int,
    episode: Episode,
    session: AsyncSession,
) -> list[str]:
    """Рефлексия одного эпизода: извлечь факты через LLM и сохранить в Memory."""
    text = episode.raw_sample or ""
    if episode.summary:
        text = f"{episode.summary}\n{text}"

    if len(text) < 30:
        return []

    try:
        from src.db.repo import get_or_create_user
        from src.llm.router import build_provider
        from src.llm.base import ChatMessage, TaskType

        owner = await get_or_create_user(session, user_id)
        provider = await build_provider(
            session, owner, purpose="background", task_type=TaskType.MEMORY
        )
        if not provider:
            return []

        prompt = (
            "Из этого разговора извлеки ВСЕ факты о пользователе, "
            "которые можно сохранить в долговременную память. "
            "Формат: один факт на строку. Только конкретные утверждения, "
            "не общие рассуждения.\n\n"
            f"РАЗГОВОР:\n{text[:2000]}\n\nФакты:"
        )
        resp = await provider.chat([ChatMessage(role="user", content=prompt)])
        if not resp:
            return []

        facts = [line.strip("- •*0123456789. ") for line in resp.split("\n")]
        facts = [f for f in facts if len(f) > 10 and len(f) < 500]

        # Сохраняем факты
        saved: list[str] = []
        from src.db.models._memory import Memory as MemoryModel

        for fact_text in facts[:5]:  # не больше 5 новых фактов за эпизод
            try:
                memory = MemoryModel(
                    user_id=user_id,
                    fact=fact_text,
                    source="auto",
                    memory_type="personal",
                    confidence=0.4,  # ниже чем у прямого извлечения
                )
                session.add(memory)
                saved.append(fact_text)
            except Exception:
                logger.debug("Failed to save reflected fact", exc_info=True)

        await session.flush()

        # Обновляем episode.memory_ids
        if saved:
            existing_ids = json.loads(episode.memory_ids) if episode.memory_ids else []
            # Добавляем новые ID (после flush у memory появится id)
            # ... IDs сложно получить после flush без повторного запроса.
            # Для MVP: обновим флаг что рефлексия была.
            pass

        return saved

    except Exception:
        logger.debug("Reflection LLM call failed for episode %d", episode.id)
        return []


# ── Auto-creation helper для free_text ────────────────────────────────────


# Счётчик сообщений для батчинга (in-memory, теряется при рестарте — OK для MVP)
_message_counter: dict[int, int] = {}
_message_buffer: dict[int, list[str]] = {}


def should_create_episode(user_id: int) -> bool:
    """Проверить, пора ли создавать эпизод (каждые N сообщений)."""
    count = _message_counter.get(user_id, 0)
    return count >= settings.episodic_batch_size


def track_message(user_id: int, text: str) -> None:
    """Учесть сообщение в счётчике и буфере."""
    _message_counter[user_id] = _message_counter.get(user_id, 0) + 1
    buf = _message_buffer.setdefault(user_id, [])
    buf.append(text)
    # Держим буфер в рамках 2x batch_size
    if len(buf) > settings.episodic_batch_size * 2:
        buf[:] = buf[-settings.episodic_batch_size :]


def reset_counter(user_id: int) -> list[str]:
    """Сбросить счётчик и вернуть накопленные сообщения для создания эпизода."""
    _message_counter[user_id] = 0
    buf = _message_buffer.pop(user_id, [])
    return buf[-settings.episodic_batch_size :]
