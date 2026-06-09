"""MCP tools для Knowledge Graph: поиск сущностей, обход графа, извлечение.

Три инструмента:
- entity_search — поиск сущностей в графе знаний
- entity_traverse — BFS-обход графа от сущности
- entity_extract — извлечение сущностей из текста фактов
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core.actions.tool_registry import tool

logger = logging.getLogger(__name__)


def _resolve_user_id(kwargs: dict[str, Any]) -> int | None:
    """Извлекает telegram_id из runtime kwargs (User ORM или telegram_id int)."""
    user_val = kwargs.get("user")
    if user_val is None:
        return None
    if hasattr(user_val, "telegram_id"):
        return int(user_val.telegram_id)  # type: ignore[union-attr]
    try:
        return int(user_val)
    except (TypeError, ValueError):
        return None


# ── entity_search ────────────────────────────────────────────────────────


@tool(
    name="entity_search",
    description=(
        "Поиск сущностей в графе знаний по имени. "
        "Находит персоны, проекты, места, компании, темы, "
        "которые упоминались в фактах о пользователе. "
        "Используй чтобы узнать, что известно о человеке, проекте или теме."
    ),
    category="memory",
    risk="low",
    params={
        "query": "str — поисковый запрос (подстрока имени)",
        "entity_type": "str|None — фильтр по типу: person, project, place, company, topic",
    },
)
async def entity_search(
    query: str,
    entity_type: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Поиск сущностей в knowledge graph."""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user не определён"}

    if not query or not query.strip():
        return {"ok": True, "entities": [], "total": 0, "query": query}

    from src.core.memory.graph_traversal import search_entity

    result = await search_entity(user_id, query=query, entity_type=entity_type)
    return result


# ── entity_traverse ──────────────────────────────────────────────────────


@tool(
    name="entity_traverse",
    description=(
        "Обход графа знаний от заданной сущности. "
        "Показывает все связанные сущности и типы связей между ними. "
        "Используй чтобы понять контекст: с кем связан человек, "
        "в каких проектах участвует, где находится. "
        "hops — глубина обхода (1-3, по умолчанию 2)."
    ),
    category="memory",
    risk="low",
    params={
        "entity_name": "str — имя сущности для старта обхода",
        "hops": "int=2 — число шагов обхода (1-3)",
    },
)
async def entity_traverse(
    entity_name: str,
    hops: int = 2,
    **kwargs: Any,
) -> dict[str, Any]:
    """Обход графа знаний от сущности."""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user не определён"}

    if not entity_name or not entity_name.strip():
        return {"ok": False, "error": "entity_name обязателен"}

    hops = max(1, min(hops, 5))

    from src.core.memory.graph_traversal import traverse

    result = await traverse(user_id, start_entity=entity_name.strip(), hops=hops)
    return result


# ── entity_extract ───────────────────────────────────────────────────────


@tool(
    name="entity_extract",
    description=(
        "Извлечь сущности и связи из текста фактов. "
        "Принимает JSON-строку с массивом фактов или текстовый блок. "
        "Возвращает найденные сущности (персоны, проекты, места, компании, темы) "
        "и связи между ними. "
        "Используй когда нужно структурировать информацию из разговора."
    ),
    category="memory",
    risk="low",
    params={
        "facts": "str — JSON-массив фактов или текстовый блок с фактами",
    },
)
async def entity_extract(
    facts: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Извлечение сущностей из текста фактов."""
    user_id = _resolve_user_id(kwargs)
    if user_id is None:
        return {"ok": False, "error": "user не определён"}

    if not facts or not facts.strip():
        return {"ok": False, "error": "facts обязателен"}

    # Парсим факты: может быть JSON-массив или plain text
    facts_list: list[str] = []
    facts_text = facts.strip()

    # Пробуем JSON
    if facts_text.startswith("["):
        try:
            parsed = json.loads(facts_text)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, str):
                        facts_list.append(item)
                    elif isinstance(item, dict):
                        facts_list.append(item.get("fact", str(item)))
        except (json.JSONDecodeError, ValueError):
            pass

    # Если не JSON — разбиваем по строкам
    if not facts_list:
        facts_list = [
            line.strip().lstrip("-•* ").strip()
            for line in facts_text.split("\n")
            if line.strip()
        ]

    if not facts_list:
        return {"ok": False, "error": "Не удалось распарсить факты"}

    from src.core.memory.entity_extractor import extract_entities

    result = await extract_entities(user_id, facts=facts_list)

    # Сохраняем результат
    entities = result.get("entities", [])
    relations = result.get("relations", [])
    if entities:
        from src.core.memory.entity_extractor import save_entities

        saved_e, saved_r = await save_entities(user_id, entities, relations)
        return {
            "ok": True,
            "entities_found": len(entities),
            "relations_found": len(relations),
            "entities_saved": saved_e,
            "relations_saved": saved_r,
            "entities": entities[:20],
            "relations": relations[:20],
            "message": (
                f"Извлечено {len(entities)} сущностей, {len(relations)} связей. "
                f"Сохранено: {saved_e} новых сущностей, {saved_r} новых связей."
            ),
        }

    return {
        "ok": True,
        "entities_found": 0,
        "relations_found": 0,
        "entities": [],
        "relations": [],
        "message": "Сущности не найдены в переданном тексте.",
    }
