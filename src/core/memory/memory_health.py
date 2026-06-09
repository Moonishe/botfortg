"""Memory Health Score — единый балл здоровья памяти 0-100."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from sqlalchemy import func, select
from src.db.session import get_session
from src.db.repo import get_or_create_user, list_memories, list_contacts
from src.db.models import MemoryLink
from src.db.repos.memory_repo import get_graph_stats
from src.core.memory.temporal_layers import compute_retention

logger = logging.getLogger(__name__)


def _ru_plural(count: int, one: str, few: str, many: str) -> str:
    """Возвращает русское окончание для числительных."""
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and (count % 100 < 10 or count % 100 >= 20):
        return few
    return many


async def calculate_health_score(owner_id: int) -> dict:
    """
    Вычисляет балл здоровья памяти и компоненты (кэшируется на 5 минут).
    Возвращает {score, confidence_score, coverage_score, freshness_score, distillation_score, diagnostics}
    """
    from src.core.actions.stats_cache import get_cached, set_cache

    cache_key = f"health:{owner_id}"
    cached = await get_cached(cache_key)
    if cached is not None:
        return cached

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        memories = await list_memories(session, owner)
        contacts = await list_contacts(
            session, owner, kinds=("user",), include_bots=False
        )

        active = [m for m in memories if m.is_active]
        all_contacts = len(contacts)
        now = datetime.now(timezone.utc)

        diagnostics = []

        # 1. Confidence Score (средний confidence активных фактов) × 100
        conf_values = [m.confidence or 0.5 for m in active]
        avg_conf = sum(conf_values) / len(conf_values) if conf_values else 0
        confidence_score = avg_conf * 100
        if confidence_score < 40:
            diagnostics.append(f"🔴 Средний confidence: {avg_conf:.2f} — низкий")
        elif confidence_score < 70:
            diagnostics.append(f"🟡 Средний confidence: {avg_conf:.2f} — средний")
        else:
            diagnostics.append(f"🟢 Средний confidence: {avg_conf:.2f} — высокий")

        # 2. Coverage Score (доля контактов с фактами) × 100
        contacts_with_facts = set()
        for m in active:
            if m.contact_id:
                contacts_with_facts.add(m.contact_id)
        coverage = len(contacts_with_facts) / max(all_contacts, 1)
        coverage_score = coverage * 100
        if coverage_score < 20:
            diagnostics.append(
                f"🔴 Покрытие: {len(contacts_with_facts)}/{all_contacts} контактов ({coverage * 100:.0f}%) — мало"
            )
        elif coverage_score < 50:
            diagnostics.append(
                f"🟡 Покрытие: {len(contacts_with_facts)}/{all_contacts} контактов ({coverage * 100:.0f}%) — средне"
            )
        else:
            diagnostics.append(
                f"🟢 Покрытие: {len(contacts_with_facts)}/{all_contacts} контактов ({coverage * 100:.0f}%) — хорошо"
            )

        # 3. Freshness Score (доля фактов младше 30 дней) × 100
        fresh_cutoff = now - timedelta(days=30)
        fresh_facts = [
            m for m in active if m.created_at and m.created_at >= fresh_cutoff
        ]
        freshness = len(fresh_facts) / max(len(active), 1)
        freshness_score = freshness * 100
        if freshness_score < 30:
            diagnostics.append(
                f"🔴 Свежесть: {len(fresh_facts)}/{len(active)} фактов младше 30 дней ({freshness * 100:.0f}%) — память застаивается"
            )
        elif freshness_score < 60:
            diagnostics.append(
                f"🟡 Свежесть: {len(fresh_facts)}/{len(active)} фактов ({freshness * 100:.0f}%) — средне"
            )
        else:
            diagnostics.append(
                f"🟢 Свежесть: {len(fresh_facts)}/{len(active)} фактов ({freshness * 100:.0f}%) — отлично"
            )

        # 4. Distillation/Structure Score (доля distillation + tier 3 фактов) × 100
        structured = [
            m for m in active if m.source == "distillation" or m.memory_tier == 3
        ]
        structure_ratio = len(structured) / max(len(active), 1)
        structure_score = min(
            structure_ratio * 200, 100
        )  # ×2 потому что distillation мало, но каждая ценна
        if structure_score < 10:
            diagnostics.append(
                f"🟡 Структурированность: {len(structured)} distillation-фактов — можно улучшить"
            )

        # 5. Tag Coverage (доля тегированных фактов)
        tagged = [m for m in active if m.tags]
        tag_ratio = len(tagged) / max(len(active), 1)
        tag_score = min(tag_ratio * 120, 100)
        if tag_score < 40:
            diagnostics.append(
                f"🟡 Теги: {len(tagged)}/{len(active)} фактов протегировано ({tag_ratio * 100:.0f}%)"
            )

        # 6. Retention Score (средняя Ebbinghaus retention активных фактов) × 100
        retention_values = [compute_retention(m, now) for m in active]
        avg_retention = (
            sum(retention_values) / len(retention_values) if retention_values else 0
        )
        retention_score = avg_retention * 100
        if retention_score < 30:
            diagnostics.append(
                f"🔴 Retention: {avg_retention:.2f} — факты быстро забываются"
            )
        elif retention_score < 60:
            diagnostics.append(
                f"🟡 Retention: {avg_retention:.2f} — средняя сохранность"
            )
        else:
            diagnostics.append(
                f"🟢 Retention: {avg_retention:.2f} — хорошая сохранность"
            )

        # 7. Contradictions warning
        contrad = await session.execute(
            select(func.count())
            .select_from(MemoryLink)
            .where(
                MemoryLink.user_id == owner.id,
                MemoryLink.relation_type == "contradicts",
            )
        )
        contradictions_count: int = contrad.scalar() or 0
        if contradictions_count > 0:
            diagnostics.append(
                f"⚠️ {contradictions_count} противоречи{_ru_plural(contradictions_count, 'й', 'я', 'й')} обнаружено в памяти"
            )

        # 8. Graph connectivity indicator
        try:
            graph_stats = await get_graph_stats(session, owner.id)
            if graph_stats["node_count"] > 0:
                diagnostics.append(
                    f"🔗 Граф: {graph_stats['node_count']} узлов, {graph_stats['total_edges']} рёбер, "
                    f"средняя степень {graph_stats['avg_degree']}"
                )
        except Exception:
            logger.debug("get_graph_stats unavailable, skipping graph indicator")

        # Композитный score: среднее взвешенное
        weights = {
            "confidence": 0.30,
            "coverage": 0.20,
            "freshness": 0.20,
            "structure": 0.10,
            "tags": 0.05,
            "retention": 0.15,
        }
        composite = (
            weights["confidence"] * confidence_score
            + weights["coverage"] * coverage_score
            + weights["freshness"] * freshness_score
            + weights["structure"] * structure_score
            + weights["tags"] * tag_score
            + weights["retention"] * retention_score
        )

        # Определяем уровень
        if composite >= 70:
            level = "green"
            emoji = "🟢🧠"
            label = "Отлично"
        elif composite >= 40:
            level = "yellow"
            emoji = "🟡"
            label = "Средне"
        else:
            level = "red"
            emoji = "🔴"
            label = "Плохо"

        result = {
            "score": round(composite, 1),
            "level": level,
            "emoji": emoji,
            "label": label,
            "confidence_score": round(confidence_score, 1),
            "coverage_score": round(coverage_score, 1),
            "freshness_score": round(freshness_score, 1),
            "structure_score": round(structure_score, 1),
            "tag_score": round(tag_score, 1),
            "retention_score": round(retention_score, 1),
            "total_facts": len(active),
            "total_contacts": all_contacts,
            "contacts_with_facts": len(contacts_with_facts),
            "diagnostics": diagnostics,
        }
        await set_cache(cache_key, result)

        # ---- Phase 2: record health metric ----
        try:
            from src.core.memory.memory_metrics import memory_metrics

            await memory_metrics.record_health(
                score=result["score"],
                components=result,
            )
        except Exception:
            pass

        return result


def format_health(health: dict) -> str:
    """Форматирует здоровье памяти в HTML."""
    score = health["score"]
    emoji = health["emoji"]
    label = health["label"]

    # Цветной progress bar
    bar_len = 10
    filled = int(score / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)

    lines = [
        f"<b>{emoji} Здоровье памяти: {score}/100 — {label} {emoji}</b>",
        f"[{bar}]",
        "",
        "📊 <b>Компоненты:</b>",
        f"  🎯 Confidence: {health['confidence_score']}/100",
        f"  🌐 Покрытие: {health['coverage_score']}/100 ({health['contacts_with_facts']}/{health['total_contacts']} контактов)",
        f"  ⏳ Свежесть: {health['freshness_score']}/100",
        f"  💡 Структура: {health['structure_score']}/100",
        f"  🏷 Теги: {health['tag_score']}/100",
        f"  🧠 Retention: {health['retention_score']}/100",
        f"  📝 Всего фактов: {health['total_facts']}",
    ]

    if health["diagnostics"]:
        lines.append("")
        lines.append("<b>🔍 Диагностика:</b>")
        for d in health["diagnostics"]:
            lines.append(f"  {d}")

    return "\n".join(lines)


def format_health_compact(health: dict) -> str:
    """Компактный индикатор для вставки в шапку брифинга."""
    score = health["score"]
    bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
    return f"🧠 Здоровье памяти: {score}/100 [{bar}]"


async def compute_emotional_trend(owner_id: int) -> str | None:
    """
    Сравнивает sentiment за последние 7 дней vs предыдущие 7 дней.
    Возвращает строку с эмоциональным трендом или None, если данных недостаточно.
    """
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)
        memories = await list_memories(session, owner)
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=7)
    prior_cutoff = now - timedelta(days=14)

    recent = [
        m
        for m in memories
        if m.is_active
        and m.sentiment in ("positive", "negative")
        and m.created_at
        and recent_cutoff <= m.created_at <= now
    ]
    prior = [
        m
        for m in memories
        if m.is_active
        and m.sentiment in ("positive", "negative")
        and m.created_at
        and prior_cutoff <= m.created_at < recent_cutoff
    ]

    if not recent or not prior:
        return None

    def positivity_ratio(ms: list) -> float:
        pos = sum(1 for m in ms if m.sentiment == "positive")
        return pos / len(ms) if ms else 0.0

    recent_ratio = positivity_ratio(recent)
    prior_ratio = positivity_ratio(prior)
    diff = recent_ratio - prior_ratio

    if diff > 0.1:
        return "📈 Эмоциональный тренд: отношения улучшаются ✨"
    elif diff < -0.1:
        return "📉 Эмоциональный тренд: растёт напряжение ⚠️"
    else:
        return "➖ Эмоциональный тренд: стабильно"


# ══════════════════════════════════════════════════════════════════════════
# Health Recommendations — actionable suggestions
# ══════════════════════════════════════════════════════════════════════════


class RecommendationSeverity(str, Enum):
    """Уровень критичности рекомендации."""

    CRITICAL = "critical"  # требует немедленного внимания
    WARNING = "warning"  # рекомендуется исправить
    INFO = "info"  # информационная, nice-to-have


@dataclass
class HealthRecommendation:
    """Одна рекомендация по улучшению здоровья памяти.

    Attributes:
        severity: Уровень критичности.
        category: Категория (freshness, coverage, structure, tags, retention, graph).
        title: Краткий заголовок рекомендации.
        description: Развёрнутое описание и конкретные действия.
        metric_name: Имя метрики, на которой основана рекомендация.
        current_value: Текущее значение метрики.
        threshold: Пороговое значение, ниже которого рекомендация активна.
    """

    severity: RecommendationSeverity
    category: str
    title: str
    description: str
    metric_name: str = ""
    current_value: float = 0.0
    threshold: float = 0.0


def generate_recommendations(
    health: dict,
    *,
    stale_fact_ratio: Optional[float] = None,
    graph_density: Optional[float] = None,
    orphan_ratio: Optional[float] = None,
) -> list[HealthRecommendation]:
    """Генерирует actionable-рекомендации на основе метрик здоровья памяти.

    Анализирует переданные метрики и возвращает список конкретных рекомендаций
    по улучшению качества памяти: свежесть, покрытие, структура, теги,
    связность графа, retention, противоречия.

    Args:
        health: Результат `calculate_health_score()` — словарь с ключами:
            score, confidence_score, coverage_score, freshness_score,
            structure_score, tag_score, retention_score, total_facts,
            total_contacts, contacts_with_facts, diagnostics.
        stale_fact_ratio: Доля устаревших фактов (>30 дней). Вычисляется как
            (1 - freshness_score/100). Если None — вычисляется из health.
        graph_density: Плотность графа памяти (avg_degree / node_count).
            Если None — рекомендации по графу не генерируются.
        orphan_ratio: Доля контактов-сирот (без фактов). Вычисляется как
            (1 - coverage_score/100). Если None — вычисляется из health.

    Returns:
        Список HealthRecommendation (может быть пустым, если всё хорошо).
    """
    recommendations: list[HealthRecommendation] = []

    # ── Вычисляем производные метрики, если не переданы ──
    freshness_score = health.get("freshness_score", 100.0)
    _stale: float = (
        stale_fact_ratio
        if stale_fact_ratio is not None
        else 1.0 - (freshness_score / 100.0)
    )

    coverage_score = health.get("coverage_score", 100.0)
    _orphan: float = (
        orphan_ratio if orphan_ratio is not None else 1.0 - (coverage_score / 100.0)
    )

    # ── 1. Свежесть (stale_fact_ratio) ──
    if _stale > 0.7:
        recommendations.append(
            HealthRecommendation(
                severity=RecommendationSeverity.CRITICAL,
                category="freshness",
                title="Критически низкая свежесть памяти",
                description=(
                    f"{_stale:.0%} фактов старше 30 дней. "
                    "Память застаивается — новые взаимодействия не фиксируются. "
                    "Рекомендуется: проверить работу экстрактора фактов, "
                    "увеличить частоту общения с контактами, "
                    "запустить принудительное извлечение фактов из истории."
                ),
                metric_name="stale_fact_ratio",
                current_value=_stale,
                threshold=0.7,
            )
        )
    elif _stale > 0.4:
        recommendations.append(
            HealthRecommendation(
                severity=RecommendationSeverity.WARNING,
                category="freshness",
                title="Свежесть памяти ниже нормы",
                description=(
                    f"{_stale:.0%} фактов устарели. "
                    "Рекомендуется: активизировать диалоги с контактами, "
                    "проверить настройки авто-сохранения фактов, "
                    "использовать `/memory extract` для принудительного обновления."
                ),
                metric_name="stale_fact_ratio",
                current_value=_stale,
                threshold=0.4,
            )
        )

    # ── 2. Покрытие / сироты (orphan_ratio) ──
    if _orphan > 0.8:
        recommendations.append(
            HealthRecommendation(
                severity=RecommendationSeverity.CRITICAL,
                category="coverage",
                title="Большинство контактов — «сироты» без фактов",
                description=(
                    f"{_orphan:.0%} контактов не имеют ни одного факта в памяти. "
                    "Рекомендуется: написать/позвонить контактам-сиротам, "
                    "импортировать историю переписки, "
                    "вручную добавить ключевые факты через `/memory add`."
                ),
                metric_name="orphan_ratio",
                current_value=_orphan,
                threshold=0.8,
            )
        )
    elif _orphan > 0.5:
        recommendations.append(
            HealthRecommendation(
                severity=RecommendationSeverity.WARNING,
                category="coverage",
                title="Много контактов без фактов",
                description=(
                    f"{_orphan:.0%} контактов не охвачены памятью. "
                    "Рекомендуется: проверить контакты без фактов через `/contacts orphans`, "
                    "запустить анализ истории чатов."
                ),
                metric_name="orphan_ratio",
                current_value=_orphan,
                threshold=0.5,
            )
        )

    # ── 3. Структурированность ──
    structure_score = health.get("structure_score", 0.0)
    if structure_score < 10.0 and health.get("total_facts", 0) > 10:
        recommendations.append(
            HealthRecommendation(
                severity=RecommendationSeverity.WARNING,
                category="structure",
                title="Мало distillation-фактов — память не структурирована",
                description=(
                    f"Структурированность: {structure_score:.0f}/100. "
                    "Рекомендуется: запустить distillation пайплайн "
                    "(`/memory distill`), увеличить приоритет извлечения "
                    "глубинных фактов в настройках."
                ),
                metric_name="structure_score",
                current_value=structure_score,
                threshold=10.0,
            )
        )

    # ── 4. Теги ──
    tag_score = health.get("tag_score", 100.0)
    if tag_score < 30.0 and health.get("total_facts", 0) > 5:
        recommendations.append(
            HealthRecommendation(
                severity=RecommendationSeverity.WARNING,
                category="tags",
                title="Низкое покрытие тегами — поиск неэффективен",
                description=(
                    f"Теги: {tag_score:.0f}/100. "
                    "Рекомендуется: включить авто-тегирование в настройках, "
                    "добавить теги к ключевым фактам вручную, "
                    "запустить ретроспективное тегирование."
                ),
                metric_name="tag_score",
                current_value=tag_score,
                threshold=30.0,
            )
        )

    # ── 5. Retention ──
    retention_score = health.get("retention_score", 100.0)
    if retention_score < 30.0:
        recommendations.append(
            HealthRecommendation(
                severity=RecommendationSeverity.CRITICAL,
                category="retention",
                title="Критически низкая сохранность фактов",
                description=(
                    f"Retention: {retention_score:.0f}/100 — факты быстро забываются. "
                    "Рекомендуется: увеличить частоту повторения "
                    "(Ebbinghaus intervals), уменьшить порог авто-забывания, "
                    "проверить настройки `auto_forget_threshold` и "
                    "`ebbinghaus_decay_base`."
                ),
                metric_name="retention_score",
                current_value=retention_score,
                threshold=30.0,
            )
        )
    elif retention_score < 60.0:
        recommendations.append(
            HealthRecommendation(
                severity=RecommendationSeverity.WARNING,
                category="retention",
                title="Сохранность фактов ниже нормы",
                description=(
                    f"Retention: {retention_score:.0f}/100. "
                    "Рекомендуется: настроить интервалы повторения, "
                    "проверить работу dreaming_reval для укрепления памяти."
                ),
                metric_name="retention_score",
                current_value=retention_score,
                threshold=60.0,
            )
        )

    # ── 6. Плотность графа ──
    if graph_density is not None:
        if graph_density < 0.5:
            recommendations.append(
                HealthRecommendation(
                    severity=RecommendationSeverity.WARNING,
                    category="graph",
                    title="Низкая плотность графа памяти",
                    description=(
                        f"Плотность графа: {graph_density:.2f} — "
                        "факты слабо связаны между собой. "
                        "Рекомендуется: запустить memory_clusterer, "
                        "проверить работу relation_extractor, "
                        "добавить перекрёстные ссылки между фактами."
                    ),
                    metric_name="graph_density",
                    current_value=graph_density,
                    threshold=0.5,
                )
            )
        elif graph_density > 5.0:
            recommendations.append(
                HealthRecommendation(
                    severity=RecommendationSeverity.INFO,
                    category="graph",
                    title="Граф памяти переуплотнён",
                    description=(
                        f"Плотность графа: {graph_density:.2f} — "
                        "слишком много связей может указывать на шум. "
                        "Рекомендуется: проверить качество связей через "
                        "`/memory contradictions`, запустить дедупликацию."
                    ),
                    metric_name="graph_density",
                    current_value=graph_density,
                    threshold=5.0,
                )
            )

    # ── 7. Confidence ──
    confidence_score = health.get("confidence_score", 100.0)
    if confidence_score < 40.0:
        recommendations.append(
            HealthRecommendation(
                severity=RecommendationSeverity.WARNING,
                category="confidence",
                title="Низкая уверенность в фактах",
                description=(
                    f"Confidence: {confidence_score:.0f}/100. "
                    "Рекомендуется: перепроверить факты с низким confidence, "
                    "увеличить порог извлечения (`extract_priority_threshold`), "
                    "подтвердить или опровергнуть сомнительные факты."
                ),
                metric_name="confidence_score",
                current_value=confidence_score,
                threshold=40.0,
            )
        )

    # ── 8. Пустой граф ──
    total_facts = health.get("total_facts", 0)
    if total_facts == 0:
        recommendations.append(
            HealthRecommendation(
                severity=RecommendationSeverity.CRITICAL,
                category="general",
                title="Память пуста — нет ни одного факта",
                description=(
                    "В памяти нет ни одного активного факта. "
                    "Рекомендуется: проверить подключение к БД, "
                    "импортировать историю чатов, "
                    "начать активное общение для накопления фактов."
                ),
                metric_name="total_facts",
                current_value=0.0,
                threshold=1.0,
            )
        )

    return recommendations
