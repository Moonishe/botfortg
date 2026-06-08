"""MCP Tools: эпизодическая память — поиск, просмотр и список эпизодов.

Инструменты:
- ``search_episodes`` — поиск по содержимому эпизодов
- ``recall_episode`` — полная информация о конкретном эпизоде
- ``list_recent_episodes`` — список последних эпизодов
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select

from src.core.actions.tool_registry import tool
from src.db.models._memory import Episode, EpisodeContact
from src.db.session import get_session

logger = logging.getLogger(__name__)


def _resolve_user_id(kwargs: dict[str, Any]) -> int | None:
    """Извлекает telegram_id из runtime kwargs.

    Поддерживает как User ORM-объект, так и telegram_id (int).
    """
    _user_val = kwargs.get("user")
    if _user_val is None:
        return None
    if hasattr(_user_val, "telegram_id"):
        return int(_user_val.telegram_id)  # type: ignore[union-attr]
    try:
        return int(_user_val)
    except (TypeError, ValueError):
        return None


def _episode_to_dict(episode: Episode) -> dict[str, Any]:
    """Сериализовать эпизод в словарь для ответа."""
    return {
        "id": episode.id,
        "user_id": episode.user_id,
        "started_at": episode.started_at.isoformat() if episode.started_at else None,
        "ended_at": episode.ended_at.isoformat() if episode.ended_at else None,
        "summary": episode.summary,
        "raw_sample": episode.raw_sample,
        "emotional_valence": episode.emotional_valence,
        "importance": episode.importance,
        "memory_ids": json.loads(episode.memory_ids) if episode.memory_ids else [],
    }


# ── Инструменты ──────────────────────────────────────────────────────────


@tool(
    name="search_episodes",
    description=(
        "Поиск по прошлым эпизодам/разговорам. "
        "Ищет по ключевым словам в саммари и содержимом эпизодов. "
        "Полезно чтобы вспомнить контекст прошлых обсуждений."
    ),
    category="memory",
    risk="low",
    params={
        "query": "str — поисковый запрос (ключевые слова)",
        "limit": "int=5 — макс. количество результатов",
    },
)
async def mcp_search_episodes(
    query: str,
    limit: int = 5,
    **kwargs: Any,
) -> dict[str, Any]:
    """Поиск эпизодов по содержимому."""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user_id не определён"}

    if not query or not query.strip():
        return {"ok": False, "error": "query обязателен"}

    try:
        from src.core.memory.episodic import search_episodes

        episodes = await search_episodes(user_id, query.strip(), limit=limit)
        return {
            "ok": True,
            "query": query,
            "count": len(episodes),
            "episodes": [_episode_to_dict(ep) for ep in episodes],
        }
    except Exception:
        logger.exception("search_episodes failed")
        return {"ok": False, "error": "Ошибка поиска эпизодов"}


@tool(
    name="recall_episode",
    description=(
        "Получить полную информацию о конкретном эпизоде по его ID. "
        "Включает саммари, эмоциональный тон, связанные факты памяти и контакты."
    ),
    category="memory",
    risk="low",
    params={
        "episode_id": "int — ID эпизода",
    },
)
async def mcp_recall_episode(
    episode_id: int,
    **kwargs: Any,
) -> dict[str, Any]:
    """Получить детали конкретного эпизода."""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user_id не определён"}

    if episode_id <= 0:
        return {"ok": False, "error": "episode_id должен быть > 0"}

    try:
        async with get_session() as session:
            result = await session.execute(
                select(Episode).where(
                    Episode.id == episode_id,
                    Episode.user_id == user_id,
                )
            )
            episode = result.scalar_one_or_none()

            if episode is None:
                return {"ok": False, "error": f"Эпизод #{episode_id} не найден"}

            # Загружаем контакты
            contacts_result = await session.execute(
                select(EpisodeContact).where(
                    EpisodeContact.episode_id == episode_id,
                )
            )
            contacts = contacts_result.scalars().all()

            # Загружаем связанные Memory-факты
            memory_ids = json.loads(episode.memory_ids) if episode.memory_ids else []
            linked_facts: list[dict] = []
            if memory_ids:
                from src.db.models._memory import Memory

                mem_result = await session.execute(
                    select(Memory).where(Memory.id.in_(memory_ids))
                )
                linked_facts = [
                    {"id": m.id, "fact": m.fact, "confidence": m.confidence}
                    for m in mem_result.scalars().all()
                ]

            return {
                "ok": True,
                "episode": _episode_to_dict(episode),
                "contacts": [
                    {
                        "id": c.id,
                        "contact_id": c.contact_id,
                        "contact_name": c.contact_name,
                        "role": c.role,
                    }
                    for c in contacts
                ],
                "linked_facts": linked_facts,
            }
    except Exception:
        logger.exception("recall_episode failed for episode %d", episode_id)
        return {"ok": False, "error": "Ошибка получения эпизода"}


@tool(
    name="list_recent_episodes",
    description=(
        "Показать список последних эпизодов/разговоров. "
        "Возвращает саммари, дату и эмоциональный тон каждого."
    ),
    category="memory",
    risk="low",
    params={
        "limit": "int=5 — количество эпизодов",
    },
)
async def mcp_list_recent_episodes(
    limit: int = 5,
    **kwargs: Any,
) -> dict[str, Any]:
    """Список последних эпизодов."""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user_id не определён"}

    try:
        from src.core.memory.episodic import get_recent_episodes

        episodes = await get_recent_episodes(user_id, limit=limit)
        return {
            "ok": True,
            "count": len(episodes),
            "episodes": [_episode_to_dict(ep) for ep in episodes],
        }
    except Exception:
        logger.exception("list_recent_episodes failed")
        return {"ok": False, "error": "Ошибка получения списка эпизодов"}
