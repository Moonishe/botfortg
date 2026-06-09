"""Извлечение сущностей и связей из фактов через LLM.

Использует существующий build_provider (тот же, что smart_extractor).
Извлекает персоны, проекты, места, компании, темы и связи между ними.
Возвращает структурированные сущности + отношения для сохранения в граф знаний.
"""

from __future__ import annotations

import json
import logging
import re

from src.llm.base import ChatMessage, TaskType

logger = logging.getLogger(__name__)

# ── Промпт для извлечения сущностей ─────────────────────────────────────

ENTITY_SYSTEM_PROMPT = (
    "Ты — анализатор текста. Извлеки из фактов именованные сущности и связи между ними.\n\n"
    "Правила:\n"
    "- Извлекай ТОЛЬКО то, что явно упомянуто в тексте.\n"
    "- Типы сущностей: person, project, place, company, topic.\n"
    "- Типы связей: works_at, friend_of, expert_in, located_in, owns, member_of, "
    "interested_in, worked_on, studies_at, lives_in, founded, manages, uses.\n"
    "- Для каждой сущности укажи имя (краткое, на русском) и тип.\n"
    "- Для каждой связи укажи source (имя первой сущности), target (имя второй), "
    "relation (тип связи) и weight (0.0-1.0 уверенность).\n\n"
    "Верни JSON-объект строго в формате:\n"
    "{\n"
    '  "entities": [\n'
    '    {"name": "имя", "type": "person|project|place|company|topic"},\n'
    "    ...\n"
    "  ],\n"
    '  "relations": [\n'
    '    {"source": "имя1", "target": "имя2", "relation": "тип_связи", "weight": 0.9},\n'
    "    ...\n"
    "  ]\n"
    "}\n\n"
    'Если сущностей нет — верни {"entities": [], "relations": []}.\n'
    "Отвечай ТОЛЬКО JSON, без обёрток и пояснений."
)


def _parse_entity_json(text: str | None) -> dict:
    """Парсит JSON-ответ LLM с извлечёнными сущностями."""
    if not text:
        return {"entities": [], "relations": []}

    text = text.strip()

    # Убираем markdown-обёртки если есть
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()

    # Ищем первый JSON-объект
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _end = decoder.raw_decode(text[match.start() :])
            if isinstance(value, dict):
                # Нормализуем ключи
                entities = value.get("entities", [])
                if not isinstance(entities, list):
                    entities = []
                relations = value.get("relations", [])
                if not isinstance(relations, list):
                    relations = []
                return {"entities": entities, "relations": relations}
        except (json.JSONDecodeError, ValueError):
            continue

    logger.debug("Entity extraction: JSON parse failed for: %.120s", text[:120])
    return {"entities": [], "relations": []}


async def extract_entities(
    user_id: int,
    facts: list[str],
    *,
    provider=None,
) -> dict:
    """Извлекает сущности и связи из списка фактов через LLM.

    Args:
        user_id: Telegram ID пользователя.
        facts: Список фактов (строк) для анализа.
        provider: Опционально — готовый LLM-провайдер. Если не передан,
                  создаётся через build_provider.

    Returns:
        dict с ключами:
          - entities: list[dict] — [{"name": "Дима", "type": "person"}, ...]
          - relations: list[dict] — [{"source": "Дима", "target": "Neurobench", "relation": "works_at", "weight": 0.9}, ...]
    """
    if not facts:
        return {"entities": [], "relations": []}

    # Объединяем факты в один текст (макс. ~2000 символов чтобы не перегружать LLM)
    facts_text = "\n".join(f"- {f}" for f in facts[:30])
    if len(facts_text) > 2500:
        facts_text = facts_text[:2500] + "\n... (обрезано)"

    # Создаём провайдер если не передан
    if provider is None:
        try:
            from src.db.repo import get_or_create_user
            from src.db.session import get_session
            from src.llm.router import build_provider

            async with get_session() as session:
                owner = await get_or_create_user(session, user_id)
                provider = await build_provider(
                    session, owner, task_type=TaskType.MEMORY
                )
        except Exception:
            logger.debug(
                "Entity extraction: build_provider failed for user %d",
                user_id,
                exc_info=True,
            )
            return {"entities": [], "relations": []}

    if provider is None:
        logger.debug("Entity extraction: no provider available for user %d", user_id)
        return {"entities": [], "relations": []}

    # LLM-вызов
    try:
        messages = [
            ChatMessage(role="system", content=ENTITY_SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=(
                    "Извлеки сущности и связи из этих фактов:\n\n"
                    f"{facts_text}\n\n"
                    "Верни ТОЛЬКО JSON."
                ),
            ),
        ]
        response_text = await provider.chat(messages, task_type=TaskType.MEMORY)
        result = _parse_entity_json(response_text)

        logger.debug(
            "Entity extraction: %d entities, %d relations for user %d",
            len(result.get("entities", [])),
            len(result.get("relations", [])),
            user_id,
        )
        return result

    except Exception:
        logger.debug(
            "Entity extraction LLM call failed for user %d",
            user_id,
            exc_info=True,
        )
        return {"entities": [], "relations": []}


async def save_entities(
    user_id: int,
    entities: list[dict],
    relations: list[dict],
) -> tuple[int, int]:
    """Сохраняет извлечённые сущности и связи в БД.

    Дедупликация по имени + типу + user_id (UPSERT-логика).
    Безопасно для параллельных вызовов (graceful skip дубликатов).

    Args:
        user_id: Telegram ID пользователя.
        entities: Список словарей с ключами name, type.
        relations: Список словарей с ключами source, target, relation, weight.

    Returns:
        (entities_saved, relations_saved) — количество новых записей.
    """
    from src.db.models._memory import Entity, EntityRelation
    from src.db.repo import get_or_create_user
    from src.db.session import get_session
    from sqlalchemy import and_, select

    entities_saved = 0
    relations_saved = 0

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, user_id)

            # ── Сохраняем сущности с дедупликацией ──
            entity_name_to_id: dict[str, int] = {}

            for e in entities:
                name = (e.get("name") or "").strip()[:128]
                etype = (e.get("type") or "topic").strip()[:32]

                if not name:
                    continue

                # Ищем существующую
                result = await session.execute(
                    select(Entity).where(
                        and_(
                            Entity.user_id == owner.id,
                            Entity.name == name,
                            Entity.type == etype,
                        )
                    )
                )
                existing = result.scalar_one_or_none()

                if existing is not None:
                    entity_name_to_id[f"{name}:{etype}"] = existing.id
                    # Обновляем metadata если передана
                    meta = e.get("metadata_json")
                    if meta and not existing.metadata_json:
                        existing.metadata_json = str(meta)[:4096]
                else:
                    new_entity = Entity(
                        user_id=owner.id,
                        name=name,
                        type=etype,
                        metadata_json=e.get("metadata_json"),
                    )
                    session.add(new_entity)
                    await session.flush()
                    entity_name_to_id[f"{name}:{etype}"] = new_entity.id
                    entities_saved += 1

            # ── Сохраняем связи ──
            for r in relations:
                source_name = (r.get("source") or "").strip()[:128]
                target_name = (r.get("target") or "").strip()[:128]
                relation = (r.get("relation") or "related_to").strip()[:64]
                weight = float(r.get("weight", 1.0))
                source_label = "extraction"

                if not source_name or not target_name:
                    continue

                # Поиск сущности по имени с учётом typed-ключа
                src_id = next(
                    (
                        eid
                        for key, eid in entity_name_to_id.items()
                        if key.startswith(f"{source_name}:")
                    ),
                    None,
                )
                tgt_id = next(
                    (
                        eid
                        for key, eid in entity_name_to_id.items()
                        if key.startswith(f"{target_name}:")
                    ),
                    None,
                )

                if src_id is None or tgt_id is None:
                    # Сущность не была в этом батче — ищем в БД
                    if src_id is None:
                        res = await session.execute(
                            select(Entity.id, Entity.type).where(
                                and_(
                                    Entity.user_id == owner.id,
                                    Entity.name == source_name,
                                )
                            )
                        )
                        row = res.first()
                        if row:
                            src_id = row[0]
                            entity_name_to_id[f"{source_name}:{row[1]}"] = src_id

                    if tgt_id is None:
                        res = await session.execute(
                            select(Entity.id, Entity.type).where(
                                and_(
                                    Entity.user_id == owner.id,
                                    Entity.name == target_name,
                                )
                            )
                        )
                        row = res.first()
                        if row:
                            tgt_id = row[0]
                            entity_name_to_id[f"{target_name}:{row[1]}"] = tgt_id

                if src_id is None or tgt_id is None:
                    continue

                # Проверяем дубликат связи
                exist_result = await session.execute(
                    select(EntityRelation).where(
                        and_(
                            EntityRelation.user_id == owner.id,
                            EntityRelation.source_id == src_id,
                            EntityRelation.target_id == tgt_id,
                            EntityRelation.relation == relation,
                        )
                    )
                )
                if exist_result.scalar_one_or_none() is not None:
                    continue  # уже есть такая связь

                new_rel = EntityRelation(
                    user_id=owner.id,
                    source_id=src_id,
                    target_id=tgt_id,
                    relation=relation,
                    weight=min(max(weight, 0.0), 1.0),
                    source=source_label,
                )
                session.add(new_rel)
                relations_saved += 1

            if entities_saved or relations_saved:
                await session.commit()
                logger.info(
                    "Knowledge graph: saved %d entities + %d relations for user %d",
                    entities_saved,
                    relations_saved,
                    user_id,
                )

    except Exception:
        logger.debug(
            "Entity save failed for user %d",
            user_id,
            exc_info=True,
        )

    return entities_saved, relations_saved


async def extract_and_save_entities(
    user_id: int,
    facts: list[str],
    *,
    provider=None,
) -> tuple[int, int]:
    """Полный пайплайн: извлечь + сохранить сущности и связи.

    Fire-and-forget обёртка — вызывается асинхронно, не блокирует
    основной поток извлечения фактов.

    Args:
        user_id: Telegram ID пользователя.
        facts: Список фактов для анализа.
        provider: Опционально — готовый LLM-провайдер.

    Returns:
        (entities_saved, relations_saved)
    """
    try:
        extracted = await extract_entities(user_id, facts, provider=provider)
        entities = extracted.get("entities", [])
        relations = extracted.get("relations", [])
        return await save_entities(user_id, entities, relations)
    except Exception:
        logger.debug(
            "extract_and_save_entities failed for user %d",
            user_id,
            exc_info=True,
        )
        return 0, 0
