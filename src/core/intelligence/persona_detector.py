"""Persona change detection — text analysis functions (no DB)."""

import logging
import re

from src.llm.base import ChatMessage
from src.core.intelligence.persona_prompts import (
    INSTRUCTION_MAP,
    MOOD_ADAPTATIONS,
    MOOD_KEYWORDS,
    RELATION_MARKERS,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Явная смена стиля (INSTRUCTION_MAP)
# ──────────────────────────────────────────────────────────────────────────────


async def detect_persona_change(user_text: str) -> dict | None:
    """Распознаёт ВСЕ изменения persona в тексте (не только первое).

    Returns:
        {"changes": dict, "auto_apply": bool, "reason": str} или None
    """
    t = user_text.lower()
    merged_changes: dict = {}
    reasons: list[str] = []

    for name, (triggers, changes) in INSTRUCTION_MAP.items():
        for trigger in triggers:
            if trigger in t:
                merged_changes.update(changes)
                reasons.append(name)
                break  # один триггер на категорию — переходим к следующей

    if not merged_changes:
        return None

    return {
        "changes": merged_changes,
        "auto_apply": True,
        "reason": ", ".join(reasons),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 2. Детекция стилевых сигналов из формы сообщения
# ──────────────────────────────────────────────────────────────────────────────


def _detect_style_signals(text: str) -> dict[str, str | None]:
    """Извлекает сигналы из формы сообщения: капс, длина, пунктуация."""
    t = text.strip()
    signals: dict[str, str | None] = {}

    # Длина сообщения
    if len(t) < 10:
        signals["brevity_hint"] = "short"
    elif len(t) > 500:
        signals["brevity_hint"] = "detailed"

    # КРИК (CAPS > 60% букв)
    alpha = [c for c in t if c.isalpha()]
    if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.6:
        signals["caps_detected"] = "urgent"

    # Многоточия → нерешительность/усталость
    if t.count("…") >= 3 or t.count("...") >= 3:
        signals["ellipsis"] = "tired"

    # Восклицательные знаки
    excl = t.count("!") + t.count("‼")
    if excl >= 3:
        signals["exclamation"] = "excited" if "👍" in t or "🔥" in t else "angry"

    # Вопросительные знаки
    qmarks = t.count("?")
    if qmarks >= 3:
        signals["question_spam"] = "anxious"
    elif qmarks >= 1 and len(t) < 30:
        signals["question_short"] = "curious"

    # Скобки-смайлы
    smiles = sum(
        1
        for s in [")", "(", ":)", ":(", "):", "(:"]
        if s in t and not any(kw in t for kw in ["гнев", "злюсь"])
    )
    if smiles >= 2:
        signals["smileys"] = "casual"

    return signals


# ──────────────────────────────────────────────────────────────────────────────
# 3. Быстрое определение настроения (keywords + сигналы)
# ──────────────────────────────────────────────────────────────────────────────


def _detect_mood_fast(text: str) -> str | None:
    """Быстрое определение настроения: keywords + сигналы формы сообщения."""
    t = text.lower()
    scores: dict[str, int] = {}

    # 1. Ключевые слова
    for mood, keywords in MOOD_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in t)
        if score > 0:
            scores[mood] = score

    # 2. Сигналы формы (усиливают или добавляют настроения)
    style = _detect_style_signals(text)

    # CAPS → urgent/stressed буст
    if style.get("caps_detected"):
        scores["urgent"] = scores.get("urgent", 0) + 2

    # Многоточия → tired
    if style.get("ellipsis"):
        scores["tired"] = scores.get("tired", 0) + 2

    # Много !! → excited или angry
    if style.get("exclamation") == "excited":
        scores["excited"] = scores.get("excited", 0) + 2
    elif style.get("exclamation") == "angry":
        scores["angry"] = scores.get("angry", 0) + 2

    # Много ?? → anxious
    if style.get("question_spam"):
        scores["anxious"] = scores.get("anxious", 0) + 2

    # Короткий вопрос → curious
    if style.get("question_short"):
        scores["curious"] = scores.get("curious", 0) + 1

    # Смайлы → casual
    if style.get("smileys"):
        scores["casual"] = scores.get("casual", 0) + 2

    if not scores:
        return None

    # Возвращаем настроение с максимальным счётом
    return max(scores, key=scores.get)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
# 4. LLM-анализ настроения (медленный, точный)
# ──────────────────────────────────────────────────────────────────────────────


async def _detect_mood_llm(text: str, provider) -> str | None:
    """LLM-анализ настроения пользователя (точный, но медленный)."""
    moods_list = ", ".join(sorted(MOOD_ADAPTATIONS.keys()))
    prompt = (
        "Проанализируй настроение пользователя по сообщению. "
        "Обрати внимание на: эмоциональный окрас, длину сообщения, "
        "пунктуацию, использование заглавных букв, сленг.\n"
        f"Ответь ОДНИМ словом из списка: {moods_list}, neutral.\n\n"
        f'Сообщение: "{text}"\n\nНастроение:'
    )
    try:
        resp = await provider.chat([ChatMessage(role="user", content=prompt)])
        mood = resp.strip().lower().rstrip(".")
        if mood in MOOD_ADAPTATIONS:
            return mood
    except Exception:
        logger.debug("LLM mood detection failed", exc_info=True)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# 5. Двухуровневый анализ настроения
# ──────────────────────────────────────────────────────────────────────────────


async def analyze_user_mood(
    telegram_id: int, user_text: str, provider=None
) -> str | None:
    """
    Определяет настроение пользователя по тексту.

    Двухуровневый анализ:
    1. Быстрый: ключевые слова (без LLM)
    2. Точный: LLM (если есть провайдер и keyword-анализ не дал однозначного результата)

    Возвращает: angry/frustrated/sad/happy/stressed/excited/tired/urgent/casual/formal/neutral/None
    """
    # Уровень 1: быстрый keyword-анализ
    mood = _detect_mood_fast(user_text)
    if mood is not None:
        return mood

    # Уровень 2: LLM-анализ (только если есть провайдер)
    if provider is not None:
        mood = await _detect_mood_llm(user_text, provider)
        if mood is not None:
            return mood

    return None


# ──────────────────────────────────────────────────────────────────────────────
# 6. Детекция контакта по тексту
# ──────────────────────────────────────────────────────────────────────────────


def _detect_contact_name(text: str) -> str | None:
    """Извлекает имя контакта из текста сообщения.

    Ищет паттерны: «напиши X», «ответь X», «скажи X», «что там с X»,
    «как дела у X», «спроси у X», «передай X», «для X».
    Возвращает raw-имя контакта или None.
    """
    patterns = [
        r"(?:напиши|отправь|черкани|сбрось|закинь)\s+([а-яёa-z]+(?:\s+[а-яёa-z]+){0,2})",
        r"(?:ответь|отвечай)\s+([а-яёa-z]+(?:\s+[а-яёa-z]+){0,2})",
        r"(?:скажи|передай|спроси\s+у)\s+([а-яёa-z]+(?:\s+[а-яёa-z]+){0,2})",
        r"(?:что\s+там\s+(?:с|у)|как\s+дела\s+(?:с|у)|как\s+там)\s+([а-яёa-z]+(?:\s+[а-яёa-z]+){0,2})",
        r"(?:для|к)\s+([а-яёa-z]+(?:\s+[а-яёa-z]+){0,2})",
        r"(?:с)\s+([А-ЯЁA-Z][а-яёa-z]+)(?:\s|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip().lower()
            # Отсекаем явно не-имена
            if name in {
                "мне",
                "себе",
                "тебе",
                "ему",
                "ей",
                "им",
                "всем",
                "туда",
                "сюда",
                "тут",
                "там",
                "это",
                "этот",
                "потом",
                "завтра",
                "сегодня",
                "уже",
                "ещё",
                "привет",
                "пока",
                "ок",
                "да",
                "нет",
            }:
                continue
            if len(name) < 2:
                continue
            return name

    return None


# ──────────────────────────────────────────────────────────────────────────────
# 7. Классификация отношений контакта
# ──────────────────────────────────────────────────────────────────────────────


def _classify_contact_relation(contact_name: str) -> str | None:
    """Классифицирует контакт по имени/роли в архетип отношений."""
    name_lower = contact_name.lower()
    for archetype, markers in RELATION_MARKERS.items():
        for marker in markers:
            if marker in name_lower:
                return archetype
    return None
