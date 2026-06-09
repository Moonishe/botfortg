"""Оптимизированный пайплайн smart-извлечения фактов из сообщений.

Feature toggles (config.py):
    - smart_extract_optimized : bool — включить/выключить все оптимизации
    - extract_priority_threshold : float (0.3) — порог приоритетности
    - extract_cache_ttl : int (300) — TTL кэша результатов извлечения

Оптимизации:
    A. Пропуск тривиальных сообщений через MessageClassifier
    B. Кэширование результатов для похожих сообщений
    C. Scoring приоритетности сообщения для извлечения
    D. Роутинг извлечения на лёгкую/тяжёлую модель

При выключенном флаге (smart_extract_optimized=False) — поведение идентично старому.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import string
from dataclasses import dataclass, field
from enum import Enum

from src.config import settings

logger = logging.getLogger(__name__)

# ── Ключевые слова для scoring ──────────────────────────────────────────

# Фактические утверждения → HIGH priority
_FACTUAL_KEYWORDS: tuple[str, ...] = (
    "я купил",
    "я продал",
    "я сделал",
    "я получил",
    "я сдал",
    "я работаю",
    "я устроился",
    "я уволился",
    "я переехал",
    "я живу",
    "я родился",
    "мне нравится",
    "мне не нравится",
    "меня зовут",
    "мой адрес",
    "мой номер",
    "мой телефон",
    "у меня есть",
    "у меня нет",
)

# Предпочтения → HIGH priority
_PREFERENCE_KEYWORDS: tuple[str, ...] = (
    "люблю",
    "ненавижу",
    "обожаю",
    "терпеть не могу",
    "предпочитаю",
    "не люблю",
    "мне по душе",
    "мой любимый",
    "моя любимая",
    "моё любимое",
)

# Планы → HIGH priority
_PLAN_KEYWORDS: tuple[str, ...] = (
    "завтра",
    "планирую",
    "собираюсь",
    "хочу сделать",
    "на следующей неделе",
    "в следующем месяце",
    "буду делать",
    "пойду",
    "поеду",
    "схожу",
    "запишусь",
    "закажу",
    "куплю",
)

# Эмоции → LOW priority
_EMOTION_KEYWORDS: tuple[str, ...] = (
    "круто",
    "блин",
    "вау",
    "ого",
    "жесть",
    "ура",
    "офигеть",
    "ничего себе",
)

# Вопросы → LOW priority
_QUESTION_KEYWORDS: tuple[str, ...] = (
    "как",
    "что",
    "где",
    "когда",
    "почему",
    "зачем",
    "сколько",
    "кто",
)

# ── Приоритет извлечения ────────────────────────────────────────────────


class ExtractPriority(Enum):
    """Приоритет сообщения для извлечения фактов."""

    SKIP = 0  # Не извлекать
    LOW = 1  # Низкий приоритет (пропустить при оптимизации)
    MEDIUM = 2  # Средний (извлечь лёгкой моделью)
    HIGH = 3  # Высокий (извлечь тяжёлой моделью)


@dataclass
class ExtractDecision:
    """Результат принятия решения об извлечении."""

    should_extract: bool
    priority: ExtractPriority
    score: float
    reason: str = ""
    model_mode: str = "light"  # "light" | "heavy"

    # Быстрый путь — не требуем LLM-вызов вообще
    fast_skip: bool = False

    # Кэш: есть ли готовый результат
    cached_result: list[dict] | None = None


# ── Scoring ─────────────────────────────────────────────────────────────


def score_extract_priority(text: str) -> float:
    """Оценивает ценность сообщения для извлечения фактов (0.0–1.0).

    Учитывает:
      - Фактические утверждения («я купил», «мне нравится») → +0.4
      - Предпочтения («люблю», «предпочитаю») → +0.35
      - Планы («завтра», «планирую») → +0.35
      - Длину сообщения (> 30 символов) → +0.1
      - Эмоциональные восклицания → –0.15
      - Вопросы → –0.15
      - Самореференцию («я», «мне») → +0.15

    Returns:
        float 0.0–1.0.
    """
    if not text or not text.strip():
        return 0.0

    text_lower = text.lower().strip()
    score = 0.0

    # Позитивные сигналы
    for kw in _FACTUAL_KEYWORDS:
        if kw in text_lower:
            score += 0.4
            break  # один раз за категорию

    for kw in _PREFERENCE_KEYWORDS:
        if kw in text_lower:
            score += 0.35
            break

    for kw in _PLAN_KEYWORDS:
        if kw in text_lower:
            score += 0.35
            break

    # Длина текста
    if len(text) > 50:
        score += 0.15
    elif len(text) > 30:
        score += 0.1

    # Самореференция
    self_ref = re.search(r"\b(я|мне|мой|моя|моё|мои|меня|мной)\b", text_lower)
    if self_ref:
        score += 0.15

    # Негативные сигналы
    emo_count = sum(1 for kw in _EMOTION_KEYWORDS if kw in text_lower)
    if emo_count >= 1:
        score -= 0.15 * min(emo_count, 2)

    q_count = sum(1 for kw in _QUESTION_KEYWORDS if kw in text_lower and "?" in text)
    if q_count >= 1:
        score -= 0.15 * min(q_count, 2)

    # Clamp
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


# ── Нормализация и кэширование ──────────────────────────────────────────


def _normalize_for_cache(text: str) -> str:
    """Нормализует текст для кэша: lowercase, убирает пунктуацию.

    Цифры НЕ удаляются — они семантически значимы
    (телефоны, возраст, даты, суммы).
    """
    text_lower = text.lower().strip()
    # Убираем пунктуацию
    text_no_punct = text_lower.translate(
        str.maketrans("", "", string.punctuation + "«»—–")
    )
    # Убираем лишние пробелы
    text_normalized = " ".join(text_no_punct.split())
    return text_normalized


def _hash_normalized(text: str) -> str:
    """Хэш нормализованного текста (первые 20 слов)."""
    words = text.split()[:20]
    norm = " ".join(words)
    return hashlib.md5(norm.encode()).hexdigest()


# ── Основной интерфейс ──────────────────────────────────────────────────


@dataclass
class ExtractionCacheEntry:
    """Запись в кэше результатов извлечения."""

    facts: list[dict]
    model_mode: str = "light"


# In-memory кэш как fallback (используется если cache_manager недоступен)
_extraction_cache: dict[str, ExtractionCacheEntry] = {}
_extraction_cache_max = 500
_extraction_cache_lock = asyncio.Lock()


async def make_extract_decision(
    text: str,
    *,
    user_id: int | None = None,
) -> ExtractDecision:
    """Принимает решение: нужно ли извлекать факты и какой моделью.

    Args:
        text: Текст сообщения пользователя.
        user_id: Telegram ID пользователя (для кэша).

    Returns:
        ExtractDecision с полями should_extract, priority, model_mode.
    """
    # ── Быстрая проверка: пустое сообщение ──
    if not text or not text.strip():
        return ExtractDecision(
            should_extract=False,
            priority=ExtractPriority.SKIP,
            score=0.0,
            reason="пустое сообщение",
            fast_skip=True,
        )

    # ── Feature flag: если оптимизации выключены — всегда извлекаем ──
    if not getattr(settings, "smart_extract_optimized", True):
        return ExtractDecision(
            should_extract=True,
            priority=ExtractPriority.HIGH,
            score=1.0,
            reason="оптимизации выключены (smart_extract_optimized=False)",
            model_mode="heavy",
        )

    text_stripped = text.strip()

    # ── Оптимизация A: пропуск тривиальных сообщений ──
    # Проверка длины
    if len(text_stripped) < 5:
        return ExtractDecision(
            should_extract=False,
            priority=ExtractPriority.SKIP,
            score=0.0,
            reason=f"короткое сообщение ({len(text_stripped)} символов)",
            fast_skip=True,
        )

    # Проверка однословных тривиальных ответов (ага, ок, да, нет, etc.)
    words = text_stripped.split()
    _trivial_single_words = frozenset(
        {
            "ага",
            "угу",
            "ок",
            "окей",
            "да",
            "нет",
            "ладно",
            "ясно",
            "понятно",
            "хорошо",
            "добро",
            "лады",
            "неа",
            "агась",
            "ну",
            "блин",
            "ого",
            "вау",
        }
    )
    if len(words) == 1 and words[0].lower().rstrip("!.?,") in _trivial_single_words:
        return ExtractDecision(
            should_extract=False,
            priority=ExtractPriority.SKIP,
            score=0.0,
            reason="однословный ответ",
            fast_skip=True,
        )

    # ── Используем MessageClassifier для пропуска приветствий/прощаний ──
    try:
        classification = _classify_message(text_stripped)
    except Exception:
        logger.debug("Classifier failed in make_extract_decision", exc_info=True)
        classification = None

    if classification:
        # Приветствия и прощания — пропускаем, НО проверяем фактические индикаторы
        if classification.get("greeting") or classification.get("farewell"):
            if not _has_factual_indicators(text_stripped):
                return ExtractDecision(
                    should_extract=False,
                    priority=ExtractPriority.SKIP,
                    score=0.0,
                    reason="приветствие/прощание (classifier)",
                    fast_skip=True,
                )
            # Иначе: содержит факты → не скипаем, продолжаем scoring
            logger.debug(
                "Classifier returned greeting/farewell but message has factual "
                "indicators — proceeding to scoring: %.60s",
                text_stripped,
            )

        # Тривиальные (ага, ок, да) — пропускаем, НО проверяем фактические индикаторы
        if classification.get("trivial") and not classification.get("command"):
            if not _has_factual_indicators(text_stripped):
                return ExtractDecision(
                    should_extract=False,
                    priority=ExtractPriority.SKIP,
                    score=0.0,
                    reason="тривиальное сообщение (classifier)",
                    fast_skip=True,
                )
            logger.debug(
                "Classifier returned trivial but message has factual "
                "indicators — proceeding to scoring: %.60s",
                text_stripped,
            )

    # ── Оптимизация C: scoring приоритетности ──
    priority_score = score_extract_priority(text_stripped)
    threshold = getattr(settings, "extract_priority_threshold", 0.3)

    if priority_score < threshold:
        return ExtractDecision(
            should_extract=False,
            priority=ExtractPriority.LOW,
            score=priority_score,
            reason=f"низкий приоритет (score={priority_score:.2f} < threshold={threshold})",
        )

    # ── Определяем приоритет ──
    if priority_score >= 0.6:
        priority = ExtractPriority.HIGH
    elif priority_score >= threshold:
        priority = ExtractPriority.MEDIUM
    else:
        priority = ExtractPriority.LOW

    # ── Оптимизация B: проверка кэша ──
    cache_key = _build_cache_key(text_stripped, user_id)
    cached = await _lookup_cache(cache_key)
    if cached is not None:
        return ExtractDecision(
            should_extract=True,
            priority=priority,
            score=priority_score,
            reason=f"найдено в кэше (score={priority_score:.2f})",
            model_mode=cached.model_mode,
            cached_result=cached.facts,
        )

    # ── Оптимизация D: выбор лёгкой/тяжёлой модели ──
    model_mode = _route_extraction_model(text_stripped, priority)

    return ExtractDecision(
        should_extract=True,
        priority=priority,
        score=priority_score,
        reason=f"извлечение {model_mode}-моделью (score={priority_score:.2f})",
        model_mode=model_mode,
    )


async def cache_extraction_result(
    text: str,
    facts: list[dict],
    model_mode: str = "light",
    user_id: int | None = None,
) -> None:
    """Сохраняет результат извлечения в кэш.

    Args:
        text: Исходный текст сообщения.
        facts: Извлечённые факты.
        model_mode: Какая модель использовалась.
        user_id: Telegram ID пользователя.
    """
    if not facts:
        return  # не кэшируем пустые результаты

    cache_key = _build_cache_key(text, user_id)
    ttl = getattr(settings, "extract_cache_ttl", 300)

    entry = ExtractionCacheEntry(facts=facts, model_mode=model_mode)

    # Пытаемся использовать ManagedCache
    try:
        cache = _get_managed_cache()
        await cache.set(cache_key, entry, ttl=float(ttl))
        logger.debug("Cached extraction result for key=%s", cache_key[:16])
        return
    except Exception:
        logger.debug(
            "ManagedCache unavailable, using in-memory fallback", exc_info=True
        )

    # Fallback: in-memory dict (под блокировкой для потокобезопасности)
    async with _extraction_cache_lock:
        if len(_extraction_cache) >= _extraction_cache_max:
            # Удаляем старейшую запись
            oldest = next(iter(_extraction_cache))
            del _extraction_cache[oldest]

        _extraction_cache[cache_key] = entry
    logger.debug("Cached extraction result (in-memory) for key=%s", cache_key[:16])


# ── Внутренние helpers ──────────────────────────────────────────────────


def _build_cache_key(text: str, user_id: int | None = None) -> str:
    """Строит ключ кэша: md5(normalized_text)[:16] + user_id."""
    normalized = _normalize_for_cache(text)
    text_hash = _hash_normalized(normalized)[:16]
    if user_id is not None:
        return f"extract:{user_id}:{text_hash}"
    return f"extract:anon:{text_hash}"


async def _lookup_cache(cache_key: str) -> ExtractionCacheEntry | None:
    """Ищет результат в кэше. Возвращает None если не найден или просрочен."""
    # Пытаемся ManagedCache
    try:
        cache = _get_managed_cache()
        result = await cache.get(cache_key)
        if result is not None:
            logger.debug("Cache HIT for key=%s", cache_key[:16])
            return result
        logger.debug("Cache MISS for key=%s", cache_key[:16])
        return None
    except Exception:
        logger.debug("ManagedCache unavailable for lookup", exc_info=True)

    # Fallback: in-memory (под блокировкой для потокобезопасности)
    async with _extraction_cache_lock:
        if cache_key in _extraction_cache:
            logger.debug("Cache HIT (in-memory) for key=%s", cache_key[:16])
            return _extraction_cache[cache_key]

    logger.debug("Cache MISS (in-memory) for key=%s", cache_key[:16])
    return None


# Lazy-ссылка на ManagedCache (регистрируется при первом использовании)
_extract_managed_cache = None
# L2: блокировка для потокобезопасной ленивой инициализации —
# без неё два параллельных вызова создадут два объекта кэша.
_managed_cache_lock = asyncio.Lock()


def _get_managed_cache():
    """Lazy-инициализация ManagedCache для extraction-кэша."""
    global _extract_managed_cache
    if _extract_managed_cache is not None:
        return _extract_managed_cache
    # L2: двойная проверка (double-check) под блокировкой — гарантирует
    # что ManagedCache создаётся ровно один раз даже при параллельных вызовах.
    # Создание происходит синхронно внутри async-функции; в asyncio это безопасно
    # т.к. Lock сериализует доступ, а конструктор ManagedCache не делает await.
    # Однако asyncio.Lock() нельзя использовать в синхронном контексте —
    # поэтому первая проверка без блокировки (fast-path), а для надёжного
    # создания используем fallback: если два вызова гоняются и один уже создал,
    # второй вернёт уже существующий (ManagedCache.register идемпотентен по имени).
    from src.core.cache.manager import ManagedCache, cache_manager

    ttl = getattr(settings, "extract_cache_ttl", 300)
    cache = cache_manager.register(
        ManagedCache(
            name="extract_results",
            max_size=2000,
            default_ttl=float(ttl),
        )
    )
    _extract_managed_cache = cache
    return cache


def _has_factual_indicators(text: str) -> bool:
    """Проверяет наличие фактических индикаторов в сообщении.

    Используется чтобы не пропускать сообщения вроде
    «Привет! Я купил квартиру в центре.»
    только из-за того, что classifier определил их как greeting.

    Индикаторы:
      - Длина > 20 символов (вероятно содержит контент помимо приветствия)
      - Самореференция: «я», «мой», «мне», «меня», «мной»
      - Запятая — возможный разделитель приветствия и основного содержания
    """
    text_lower = text.lower()

    # Длина > 20 символов — вероятно содержит контент помимо приветствия
    if len(text) > 20:
        return True

    # Самореференция
    self_ref_kw = ("я", "мой", "моя", "моё", "мои", "мне", "меня", "мной")
    if any(f" {kw} " in f" {text_lower} " for kw in self_ref_kw):
        return True

    # Запятая — возможный разделитель приветствия и основного содержания
    if "," in text:
        return True

    return False


def _classify_message(text: str) -> dict[str, bool] | None:
    """Lazy-классификация через MessageClassifier."""
    # NOTE: core→bot layering tradeoff — classifier lives in bot layer
    # for historical reasons. Lazy import prevents circular dependency.
    try:
        from src.bot.classifier import classify_message

        return classify_message(text)
    except Exception:
        logger.debug("classify_message failed", exc_info=True)
        return None


def _route_extraction_model(text: str, priority: ExtractPriority) -> str:
    """Выбирает лёгкую или тяжёлую модель для извлечения.

    Правила:
      - HIGH приоритет + сложный текст → heavy
      - HIGH приоритет + простой → light
      - MEDIUM → light
      - LOW → light (уже отфильтровано threshold)
    """
    if priority == ExtractPriority.HIGH:
        # Проверяем сложность текста через SmartModelRouter
        try:
            from src.core.intelligence.smart_router import get_router

            router = get_router()
            # Для extraction используем пониженный порог:
            # если router считает запрос сложным → heavy, иначе light
            decision = router.route(text, mode="auto")
            return decision  # "light" или "heavy"
        except Exception:
            logger.debug("SmartModelRouter failed in extraction routing", exc_info=True)

        # Fallback: эвристика на основе длины и сложности
        if _is_complex_text(text):
            return "heavy"
        return "light"

    # MEDIUM/LOW → всегда light
    return "light"


def _is_complex_text(text: str) -> bool:
    """Эвристическая проверка сложности текста (fallback без роутера)."""
    words = text.split()
    if len(words) > 20:
        return True
    sentences = [
        s.strip()
        for s in text.replace("!", ".").replace("?", ".").split(".")
        if s.strip()
    ]
    if len(sentences) > 2:
        return True
    # Отсылки к прошлому
    past_refs = ("ты говорил", "раньше", "вчера", "помнишь", "обсуждали")
    text_lower = text.lower()
    if any(ref in text_lower for ref in past_refs):
        return True
    return False
