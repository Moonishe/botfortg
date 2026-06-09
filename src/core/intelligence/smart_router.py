"""Smart LLM Model Router — выбор лёгкой/тяжёлой модели по сложности запроса.

Decision tree:
  1. Greeting/farewell/trivial → LIGHT (всегда простое)
  2. Single-clause question → LIGHT
  3. Multi-clause/contextual question → HEAVY (нужен контекст)
  4. Commands requiring reasoning → HEAVY
  5. Unknown → default to configured mode

Использует:
  - MessageClassifier для категоризации
  - Эвристическую оценку сложности (0–100)

Порог принятия решения:
  - score < 30 → "light"
  - score >= 30 → "heavy"
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Порог сложности ───────────────────────────────────────────────────
_COMPLEXITY_THRESHOLD: int = 30

# ── Ключевые слова для детекции отсылок к прошлому ─────────────────────
_PAST_REFERENCE_WORDS: tuple[str, ...] = (
    "ты говорил",
    "ты сказал",
    "раньше",
    "вчера",
    "на прошлой",
    "помнишь",
    "вспомни",
    "обсуждали",
    "ты упоминал",
    "ты рассказывал",
)

# ── Регулярное выражение для дат (DD.MM.YYYY, DD-MM-YYYY, DD/MM/YY) ────
_DATE_RE: re.Pattern[str] = re.compile(r"\d{2}[./-]\d{2}[./-]\d{2,4}")


class SmartModelRouter:
    """Маршрутизирует LLM-запросы к лёгкой или тяжёлой модели
    на основе категории сообщения и эвристической оценки сложности.

    Usage:
        router = SmartModelRouter()
        model_mode = router.route("расскажи подробнее про проект Neurobench")
        # → "heavy"
    """

    def __init__(self) -> None:
        self.classifier = _get_classifier()

    def route(self, text: str, mode: str = "auto") -> str:
        """Вернуть рекомендацию: 'light' или 'heavy'.

        Args:
            text: Текст сообщения пользователя.
            mode: 'auto' (умная маршрутизация), 'light' (принудительно лёгкая),
                  'heavy' (принудительно тяжёлая).

        Returns:
            'light' или 'heavy'.
        """
        # Явный режим — без логики
        if mode == "light":
            return "light"
        if mode == "heavy":
            return "heavy"

        # Режим auto — умная маршрутизация
        if not text or not text.strip():
            return "light"

        # Классифицируем
        cls_result = self.classifier.classify(text)

        # Приветствия / прощания / тривиальные — всегда лёгкая модель
        if cls_result.get("greeting") or cls_result.get("farewell"):
            return "light"

        # Считаем сложность
        score = _calc_complexity(text, cls_result)

        decision = "heavy" if score >= _COMPLEXITY_THRESHOLD else "light"
        logger.debug(
            "SmartModelRouter: score=%d → %s (text=%.50s…)",
            score,
            decision,
            text,
        )
        return decision

    @property
    def is_available(self) -> bool:
        """Проверяет, что классификатор готов к работе."""
        return self.classifier.category_count > 0


# ── Module-level singleton ─────────────────────────────────────────────
_router: SmartModelRouter | None = None


def get_router() -> SmartModelRouter:
    """Вернуть глобальный экземпляр SmartModelRouter (lazy init)."""
    global _router
    if _router is None:
        _router = SmartModelRouter()
    return _router


# ── Lazy import classifier ────────────────────────────────────────────


def _get_classifier():
    """Lazy-импорт MessageClassifier (тяжёлая зависимость)."""
    # NOTE: core→bot layering tradeoff — classifier lives in bot layer.
    # Lazy import prevents circular dependency.
    from src.bot.classifier import get_classifier

    return get_classifier()


# ── Эвристика сложности ───────────────────────────────────────────────


def _calc_complexity(text: str, classifier_result: dict[str, Any]) -> int:
    """Вычислить оценку сложности сообщения (0–100).

    Учитывает:
      - Длину текста (слов)
      - Количество предложений
      - Вопросительные знаки
      - Отсылки к прошлому (нужен контекст памяти)
      - Именованные сущности (@mentions, заглавные буквы)
      - Даты, URL, числа
      - Эмоциональные восклицания (снижают сложность)

    Args:
        text: Текст сообщения.
        classifier_result: Результат MessageClassifier.classify().

    Returns:
        Целое число 0–100.
    """
    score = 0
    words = text.split()

    # ── Длина текста ──
    if len(words) > 15:
        score += 20
    elif len(words) >= 8:
        score += 10

    # ── Сложность предложений ──
    sentences = [
        s.strip()
        for s in text.replace("!", ".").replace("?", ".").split(".")
        if s.strip()
    ]
    if len(sentences) > 2:
        score += 15
    elif len(sentences) > 1:
        score += 5

    # ── Вопросительная сложность ──
    q_marks = text.count("?")
    if q_marks > 1:
        score += 10

    # ── Отсылки к прошлому (нужен memory context → сложнее) ──
    text_lower = text.lower()
    if any(pw in text_lower for pw in _PAST_REFERENCE_WORDS):
        score += 25

    # ── Именованные сущности (нужен contact resolution) ──
    if "@" in text or _has_capitalized_words(text):
        score += 10

    # ── Данные (даты, URL, числа) ──
    if _DATE_RE.search(text):
        score += 10
    if "http" in text_lower:
        score += 10
    if len(re.findall(r"\d+", text)) > 3:
        score += 10

    # ── Эмоциональные восклицания → обычно проще ──
    if text.count("!") > 0:
        score -= 5

    # ── Переопределения классификатора ──
    if classifier_result.get("trivial"):
        score = max(0, score - 20)

    # Команды, требующие reasoning → HEAVY
    if classifier_result.get("command"):
        score = max(score, 40)

    return max(0, min(100, score))


def _has_capitalized_words(text: str) -> bool:
    """Проверяет наличие слов с заглавной буквы (имена собственные).

    Игнорирует первый символ строки и слова после точки/воскл/вопр знака
    (они и так с заглавной).
    """
    # Убираем первое слово и слова после знаков препинания
    cleaned = re.sub(r"(?:^|\s*[.!?]\s+)\w+", "", text, count=10)
    # Проверяем, есть ли слова с заглавной буквой (кроме первого символа)
    for word in cleaned.split():
        if word and word[0].isupper():
            return True
    return False
