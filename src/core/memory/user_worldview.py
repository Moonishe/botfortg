"""User Worldview — связная модель знаний о пользователе (Phase 3).

Собирает все факты памяти пользователя и строит структурированную картину:
- Категории: убеждения, предпочтения, личная информация, привычки, работа
- Противоречия и история изменений (supersedes chains)
- Сводка для персонализации ответов бота

Использование:
    from src.core.memory.user_worldview import build_worldview

    worldview = await build_worldview(owner_id)
    print(worldview.summary())
    print(worldview.categories["preferences"])
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from src.db.models import Memory, MemoryLink
from src.db.repo import get_or_create_user, list_memories
from src.db.session import get_session
from src.core.memory.memory_chain import follow_supersedes_chain
from src.core.memory.relation_types import RelationType

logger = logging.getLogger(__name__)

# Приоритет категорий
CATEGORY_ORDER = [
    "beliefs",  # убеждения (я считаю, по-моему, я думаю)
    "preferences",  # предпочтения (я люблю, ненавижу)
    "personal_info",  # личные данные (я живу в, мне N лет)
    "work",  # работа/учёба (я работаю, я учусь)
    "habits",  # привычки (я всегда, обычно)
    "relations",  # отношения (мой друг, коллега)
    "goals",  # цели (я хочу, планирую)
    "health",  # здоровье
    "misc",  # прочее
]

# Ключевые слова для категоризации
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "beliefs": [
        "считаю",
        "думаю",
        "по-моему",
        "убеждён",
        "уверен",
        "верю",
        "моё мнение",
        "по моему",
        "имхо",
    ],
    "preferences": [
        "люблю",
        "ненавижу",
        "обожаю",
        "нравится",
        "не нравится",
        "предпочитаю",
        "терпеть не могу",
        "фанат",
        "не люблю",
        "любимый",
        "нелюбимый",
    ],
    "personal_info": [
        "живу",
        "родился",
        "мне",
        "лет",
        "год",
        "зовут",
        "адрес",
        "город",
        "страна",
        "день рождения",
        "возраст",
    ],
    "work": [
        "работаю",
        "учусь",
        "должность",
        "компания",
        "зарплата",
        "офис",
        "коллеги",
        "начальник",
        "проект",
        "студент",
        "фриланс",
        "стартап",
    ],
    "habits": [
        "всегда",
        "обычно",
        "каждый день",
        "по привычке",
        "регулярно",
        "привык",
        "часто",
        "никогда не",
    ],
    "relations": [
        "друг",
        "подруга",
        "жена",
        "муж",
        "мама",
        "папа",
        "брат",
        "сестра",
        "коллега",
        "знакомый",
        "отношения",
        "парень",
        "девушка",
    ],
    "goals": [
        "хочу",
        "планирую",
        "цель",
        "мечтаю",
        "стремлюсь",
        "добиться",
        "в планах",
        "собираюсь",
    ],
    "health": [
        "болит",
        "здоровье",
        "врач",
        "больница",
        "аллергия",
        "болезнь",
        "диагноз",
        "лекарство",
        "сплю",
        "бессонница",
    ],
}


@dataclass
class WorldviewCategory:
    """Одна категория фактов."""

    name: str
    label: str
    facts: list[dict] = field(default_factory=list)
    fact_count: int = 0
    active_count: int = 0
    summary: str = ""


@dataclass
class UserWorldview:
    """Полная модель знаний о пользователе."""

    user_id: int
    generated_at: str = ""

    total_facts: int = 0
    active_facts: int = 0
    inactive_facts: int = 0

    categories: dict[str, WorldviewCategory] = field(default_factory=dict)

    contradictions: list[dict] = field(default_factory=list)
    active_contradiction_count: int = 0

    evolution_chains: list[dict] = field(default_factory=list)
    supersedes_count: int = 0

    dominant_memory_types: dict[str, int] = field(default_factory=dict)

    health_summary: dict = field(default_factory=dict)

    def summary(self) -> str:
        """Человекочитаемая сводка мировоззрения."""
        lines = [
            f"=== Мировоззрение пользователя #{self.user_id} ===",
            f"Фактов: {self.active_facts} активных / {self.total_facts} всего",
            f"Противоречий: {self.active_contradiction_count}",
            f"Цепочек эволюции: {self.supersedes_count}",
            "",
        ]
        for cat_name in CATEGORY_ORDER:
            cat = self.categories.get(cat_name)
            if cat and cat.fact_count > 0:
                lines.append(f"[{cat.label}] ({cat.active_count} активных)")
                for f in cat.facts[:5]:
                    active_mark = "" if f.get("is_active", True) else " [неакт.]"
                    superseded_mark = (
                        " (устарело)" if not f.get("is_active", True) else ""
                    )
                    lines.append(f"  - {f['fact']}{superseded_mark}")
                if cat.fact_count > 5:
                    lines.append(f"  ... ещё {cat.fact_count - 5}")
                lines.append("")
        return "\n".join(lines)


def _categorize_fact(fact_text: str) -> str:
    """Определяет категорию факта по ключевым словам."""
    lower = fact_text.lower()
    for cat_name in CATEGORY_ORDER:
        if cat_name == "misc":
            continue
        keywords = CATEGORY_KEYWORDS.get(cat_name, [])
        for kw in keywords:
            if kw in lower:
                return cat_name
    return "misc"


def _fact_to_dict(m: Memory) -> dict[str, Any]:
    """Memory → dict для worldview."""
    return {
        "id": m.id,
        "fact": m.fact or "",
        "is_active": bool(m.is_active),
        "confidence": m.confidence or 0.0,
        "memory_type": m.memory_type or "personal",
        "memory_tier": m.memory_tier or 1,
        "tags": m.tags or "",
        "created_at": m.created_at.isoformat() if m.created_at else "",
        "use_count": m.use_count or 0,
        "importance": m.importance or 0.0,
        "relation_type": m.relation_type or "",
        "related_memory_id": m.related_memory_id,
    }


async def build_worldview(owner_id: int) -> UserWorldview:
    """Строит полную модель мировоззрения пользователя.

    Возвращает UserWorldview со всеми категориями, противоречиями,
    цепочками эволюции и сводкой здоровья.
    """
    worldview = UserWorldview(user_id=owner_id)
    worldview.generated_at = datetime.now(timezone.utc).isoformat()

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)

        # 1. Все факты
        all_memories = await list_memories(session, owner)
        worldview.total_facts = len(all_memories)
        active = [m for m in all_memories if m.is_active]
        inactive = [m for m in all_memories if not m.is_active]
        worldview.active_facts = len(active)
        worldview.inactive_facts = len(inactive)

        # 2. Поиск supersedes-связей
        link_query = select(MemoryLink).where(
            MemoryLink.user_id == owner.id,
            MemoryLink.relation_type == RelationType.SUPERSEDES,
        )
        link_result = await session.execute(link_query)
        supersedes_links: list[MemoryLink] = list(link_result.scalars().all())

        worldview.supersedes_count = len(supersedes_links)

        # Собираем memory_id, которые являются хвостами supersedes (target_id)
        # Это факты, которые были заменены
        superseded_ids: set[int] = {link.target_id for link in supersedes_links}

        # 3. Категоризация
        cats: dict[str, WorldviewCategory] = {}
        for cat_name in CATEGORY_ORDER:
            label = {
                "beliefs": "Убеждения",
                "preferences": "Предпочтения",
                "personal_info": "Личное",
                "work": "Работа/Учёба",
                "habits": "Привычки",
                "relations": "Отношения",
                "goals": "Цели",
                "health": "Здоровье",
                "misc": "Прочее",
            }.get(cat_name, cat_name)
            cats[cat_name] = WorldviewCategory(name=cat_name, label=label)

        # Типы
        type_counts: dict[str, int] = {}

        for m in all_memories:
            fact_text = m.fact or ""
            cat = _categorize_fact(fact_text)
            if cat not in cats:
                cat = "misc"
            fd = _fact_to_dict(m)
            # Отмечаем заменённые факты
            if m.id in superseded_ids:
                fd["superseded"] = True
            cats[cat].facts.append(fd)
            if m.is_active:
                cats[cat].active_count += 1

            # Типы
            mt = m.memory_type or "personal"
            type_counts[mt] = type_counts.get(mt, 0) + 1

        for cat in cats.values():
            cat.fact_count = len(cat.facts)
            # Сортируем: активные сверху, потом по created_at
            cat.facts.sort(
                key=lambda f: (not f.get("is_active", True), f.get("created_at", "")),
                reverse=False,
            )

        worldview.categories = cats
        worldview.dominant_memory_types = dict(
            sorted(type_counts.items(), key=lambda x: -x[1])
        )

        # 4. Противоречия
        # Находим все факты с relation_type = "contradicts"
        # и которые являются source в supersedes (противоречие разрешено)
        contradict_links = await session.execute(
            select(MemoryLink).where(
                MemoryLink.user_id == owner.id,
                MemoryLink.relation_type == RelationType.CONTRADICTS,
            )
        )
        contradict_list = contradict_links.scalars().all()

        for link in contradict_list:
            # Проверяем, разрешено ли противоречие через supersedes
            # (source того же link — target в supersedes)
            resolved = link.source_id in superseded_ids
            worldview.contradictions.append(
                {
                    "source_id": link.source_id,
                    "target_id": link.target_id,
                    "weight": link.weight,
                    "resolved": resolved,
                }
            )

        worldview.active_contradiction_count = sum(
            1 for c in worldview.contradictions if not c["resolved"]
        )

        # 5. Цепочки эволюции (для активных supersedes-связей)
        # Находим начальные узлы (те, которые не являются source ни в одном supersedes)
        source_ids = {link.source_id for link in supersedes_links}
        tail_ids = {
            link.target_id
            for link in supersedes_links
            if link.target_id not in source_ids
        }

        for tail_id in list(tail_ids)[:5]:  # макс 5 цепочек для сводки
            try:
                chain = await follow_supersedes_chain(session, owner, tail_id)
                if len(chain) > 1:
                    worldview.evolution_chains.append(
                        {
                            "chain": chain,
                            "length": len(chain),
                            "is_evolving": chain[-1].get("is_head", False),
                        }
                    )
            except Exception:
                logger.debug(
                    "failed to build supersedes chain for memory %d",
                    tail_id,
                    exc_info=True,
                )

        # 6. Здоровье памяти (пробуем импортировать)
        try:
            from src.core.memory.memory_health import calculate_health_score

            health = await calculate_health_score(owner_id)
            worldview.health_summary = {
                "score": health.get("score", 0),
                "confidence": health.get("confidence_score", 0),
                "coverage": health.get("coverage_score", 0),
                "freshness": health.get("freshness_score", 0),
                "structure": health.get("structure_score", 0),
                "retention": health.get("retention_score", 0),
                "diagnostics": health.get("diagnostics", []),
            }
        except Exception:
            logger.debug("health score unavailable", exc_info=True)
            worldview.health_summary = {"score": -1, "error": "unavailable"}

    return worldview


def get_top_categories(worldview, top_n=3) -> list[str]:
    """Top-N категорий по количеству фактов."""
    if not worldview or not worldview.categories:
        return []
    scored = [
        (cat, facts.fact_count)
        for cat, facts in worldview.categories.items()
        if facts.fact_count > 0
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [cat for cat, _ in scored[:top_n]]


def boost_facts_by_worldview(facts: list, worldview, boost: float = 0.10) -> list:
    """Повышает score фактов из топ-категорий worldview. In-place."""
    if not worldview or not boost:
        return facts
    top_cats = get_top_categories(worldview, top_n=3)
    if not top_cats:
        return facts
    keywords = set()
    for cat in top_cats:
        cat_keywords = CATEGORY_KEYWORDS.get(cat, [])
        keywords.update(cat_keywords)
    if not keywords:
        return facts
    for fact_item in facts:
        fact_text = ""
        if hasattr(fact_item, "fact"):
            fact_text = fact_item.fact
        elif isinstance(fact_item, dict):
            fact_text = fact_item.get("fact", "")
        if not fact_text:
            continue
        fact_lower = fact_text.lower()
        for kw in keywords:
            if kw in fact_lower:
                if hasattr(fact_item, "confidence"):
                    fact_item.confidence = min(1.0, fact_item.confidence + boost)
                elif isinstance(fact_item, dict):
                    fact_item["confidence"] = min(
                        1.0, fact_item.get("confidence", 0.5) + boost
                    )
                break
    return facts
