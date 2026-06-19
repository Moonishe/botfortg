"""Helpers for scoring the quality of auto-extracted memory facts."""

from typing import Any

AUTO_SAVE_SOURCE_QUALITY = 0.4


def score_extraction_clarity(fact_text: str) -> tuple[float, float]:
    """Оценивает качество извлечённого факта и возвращает (quality, confidence)."""
    text_lower = fact_text.lower()
    quality = 0.5

    direct_markers = (
        "работает в",
        "работаю в",
        "живёт в",
        "живу в",
        "зовут",
        "года",
        "лет",
        "день рождения",
        "работает",
        "работаю",
        "учится",
        "учусь",
        "любит",
        "люблю",
        "не любит",
        "не люблю",
        "хочет",
        "хочу",
    )
    uncertain_markers = (
        "наверное",
        "возможно",
        "может быть",
        "кажется",
        "вроде",
        "скорее всего",
        "думаю",
        "по-моему",
        "не уверен",
        "не знаю",
    )

    direct_count = sum(1 for m in direct_markers if m in text_lower)
    uncertain_count = sum(1 for m in uncertain_markers if m in text_lower)

    if direct_count > 0:
        quality += 0.2 * min(direct_count, 2)
    if uncertain_count > 0:
        quality -= 0.15 * min(uncertain_count, 2)

    word_count = len(fact_text.split())
    if 3 <= word_count <= 8:
        quality += 0.1

    quality = max(0.2, min(1.0, quality))
    confidence = max(0.3, quality - 0.1)
    return round(quality, 2), round(confidence, 2)


def enrich_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Фильтрует и обогащает факты quality-оценками для batch-сохранения.

    Устанавливает source_quality=AUTO_SAVE_SOURCE_QUALITY (0.4), как у auto-фактов.
    """
    enriched: list[dict[str, Any]] = []
    for f in facts:
        if not isinstance(f, dict):
            continue
        fact_text = f.get("fact", "").strip()
        if not fact_text or len(fact_text) < 5:
            continue
        sentiment = f.get("sentiment", "neutral")
        if sentiment not in ("positive", "negative", "neutral"):
            sentiment = "neutral"
        extraction_quality, confidence = score_extraction_clarity(fact_text)
        enriched.append(
            {
                "fact": fact_text,
                "sentiment": sentiment,
                "confidence": confidence,
                "source_quality": AUTO_SAVE_SOURCE_QUALITY,
                "extraction_quality": extraction_quality,
            }
        )
    return enriched
