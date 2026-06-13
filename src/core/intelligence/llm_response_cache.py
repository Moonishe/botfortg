"""LLM Response Cache (SmartCache) — кэширует ответы LLM для семантически похожих запросов.

Два уровня кэша:
  Tier 1: Exact match (O(1)) — sha256-хэш для идентичного текста.
  Tier 2: Semantic match (O(1)) — для запросов одной категории со схожей структурой
           (например, разные приветствия → один закэшированный ответ).

Дизайн ключей:
  - Exact:  sha256(text[:200].lower().strip())
  - Semantic: f"sem:{category}" — для тривиальных категорий (greeting, farewell, ...)
              f"sem:{category}:{normalized_hash}" — для questions с нормализованной структурой

TTL-стратегия:
  - Приветствия/прощания: 1 час (редко меняются)
  - Простые вопросы: 10 минут
  - Общие ответы: 5 минут
  - НИКОГДА не кэшировать: контекстно-зависимые запросы
    (ссылки на прошлое, имена, конкретные данные)
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Final

from src.config import settings
from src.core.cache.manager import ManagedCache, cache_manager

logger = logging.getLogger(__name__)

# ── Константы TTL (секунды) ───────────────────────────────────────────

_TTL_GREETING: Final[int] = 3600  # 1 час
_TTL_QUESTION: Final[int] = 600  # 10 минут
_TTL_DEFAULT: Final[int] = 300  # 5 минут
_TTL_TRIVIAL: Final[int] = 1800  # 30 минут — agreement/disagreement/emotion

# ── Тривиальные категории: blanket-ключ (все запросы категории → один ответ) ──

_BLANKET_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "greeting",
        "farewell",
        "agreement",
        "disagreement",
        "gratitude",
        "emotion",
    }
)

# ── Категории, для которых semantic-кэш использует нормализованный хэш ──

_NORMALIZED_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "question",
    }
)

# ── Словарь распространённых русских имён для нормализации ────────────

_COMMON_RUSSIAN_NAMES: Final[frozenset[str]] = frozenset(
    {
        # Полные имена
        "александр",
        "алексей",
        "александра",
        "алёна",
        "анастасия",
        "андрей",
        "анна",
        "антон",
        "артём",
        "артемий",
        "борис",
        "вадим",
        "валентин",
        "валентина",
        "валерий",
        "василий",
        "вера",
        "виктор",
        "виктория",
        "владимир",
        "владислав",
        "галина",
        "георгий",
        "григорий",
        "дарья",
        "денис",
        "дмитрий",
        "евгений",
        "евгения",
        "екатерина",
        "елена",
        "елизавета",
        "иван",
        "игорь",
        "илья",
        "ирина",
        "кирилл",
        "константин",
        "ксения",
        "лев",
        "леонид",
        "любовь",
        "людмила",
        "максим",
        "маргарита",
        "марина",
        "мария",
        "михаил",
        "надежда",
        "наталья",
        "никита",
        "николай",
        "оксана",
        "олег",
        "ольга",
        "павел",
        "пётр",
        "полина",
        "роман",
        "руслан",
        "светлана",
        "семён",
        "сергей",
        "софия",
        "станислав",
        "таисия",
        "тамара",
        "татьяна",
        "тимофей",
        "фёдор",
        "юлия",
        "юрий",
        "яна",
        "ярослав",
        # Краткие формы
        "саша",
        "саня",
        "шура",
        "лёша",
        "алёша",
        "маша",
        "паша",
        "даша",
        "катя",
        "лена",
        "оля",
        "таня",
        "наташа",
        "дима",
        "вова",
        "женя",
        "костя",
        "миша",
        "петя",
        "света",
        "серёжа",
        "ваня",
        "коля",
        "толя",
        "влад",
        "ксюша",
        "настя",
        "вика",
        "юля",
        "ира",
        "зоя",
        "галя",
        "римма",
        "надя",
        "люба",
        "лида",
        "рая",
        "тоня",
        "валя",
        # Английские имена (на случай смешанного общения)
        "alex",
        "alexander",
        "alice",
        "andrew",
        "anna",
        "ben",
        "bob",
        "charlie",
        "dave",
        "david",
        "emma",
        "emily",
        "frank",
        "george",
        "harry",
        "jack",
        "james",
        "jane",
        "joe",
        "john",
        "kate",
        "lisa",
        "mark",
        "mary",
        "mike",
        "nick",
        "oliver",
        "paul",
        "sam",
        "sarah",
        "tom",
        "will",
    }
)

# ── Паттерны для нормализации ─────────────────────────────────────────

_RE_DATE_PATTERNS: Final[list[re.Pattern[str]]] = [
    # "вчера", "сегодня", "завтра", "позавчера", "послезавтра"
    re.compile(r"\b(вчера|сегодня|завтра|позавчера|послезавтра)\b"),
    # 01.01.2024, 1.1.24, 01/01/2024
    re.compile(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b"),
    # 1 января 2024
    re.compile(
        r"\b\d{1,2}\s+"
        r"(январ[ьяю]|феврал[ьяю]|март[ау]?|апрел[ьяю]|ма[йяю]|июн[ьяю]|"
        r"июл[ьяю]|август[ау]?|сентябр[ьяю]|октябр[ьяю]|ноябр[ьяю]|декабр[ьяю])"
        r"(?:\s+\d{2,4})?\b"
    ),
    # ISO даты: 2024-01-15
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
]

_RE_URL: Final[re.Pattern[str]] = re.compile(r"https?://\S+")

_RE_NUMBER: Final[re.Pattern[str]] = re.compile(r"\b\d+(?:[.,]\d+)?\b")

# ── Ключевые слова, указывающие на контекстную зависимость ────────────

_CONTEXT_DEPENDENT_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "ты говорил",
        "ты сказал",
        "ты писал",
        "ты упоминал",
        "помнишь",
        "ты помнишь",
        "вспомни",
        "раньше",
        "до этого",
        "перед этим",
        "в прошлый раз",
        "тогда",
    }
)

# ── Основной класс ────────────────────────────────────────────────────


class LLMResponseCache:
    """Двухуровневый кэш LLM-ответов.

    Использует :class:`ManagedCache` с TTL-инвалидацией из ``cache_manager``.

    Пример:
        cache = LLMResponseCache()
        # Проверка кэша перед LLM-вызовом
        cached = await cache.get("Привет, как дела?")
        if cached:
            return cached

        # ... LLM-вызов ...
        response = "Отлично, спасибо!"

        # Кэширование ответа
        await cache.set("Привет, как дела?", response)
    """

    def __init__(self, cm=None):
        """Инициализировать кэш.

        Args:
            cm: Опциональный :class:`CacheManager`. Если не указан,
                используется глобальный ``cache_manager``.
        """
        cm = cm if cm is not None else cache_manager
        self._cache: ManagedCache[str, str] = cm.register(
            ManagedCache[str, str](
                name="llm_response",
                max_size=5000,
                default_ttl=float(_TTL_DEFAULT),
            )
        )
        # Ленивый импорт классификатора — не тянем лишние зависимости при импорте модуля
        self._classifier = None

    @property
    def classifier(self):
        """Ленивый доступ к классификатору сообщений."""
        # NOTE: core→bot layering tradeoff — classifier lives in bot layer.
        # Lazy import prevents circular dependency.
        if self._classifier is None:
            from src.core.classification import get_classifier

            self._classifier = get_classifier()
        return self._classifier

    # ── Public API ────────────────────────────────────────────────────

    async def get(
        self,
        text: str,
        classifier_result: dict[str, bool] | None = None,
    ) -> str | None:
        """Попытаться получить закэшированный ответ.

        Проверяет два уровня:
        1. Exact match — sha256-хэш текста.
        2. Semantic match — категория + нормализованная структура.

        Args:
            text: Текст сообщения пользователя.
            classifier_result: Опциональный результат классификации.
                Если не передан — классификатор вызывается лениво.

        Returns:
            Закэшированный ответ или None при промахе.
        """
        if not settings.response_cache_enabled:
            return None

        if not text or not text.strip():
            return None

        # Tier 1: Exact match
        exact_key = self._exact_key(text)
        cached = await self._cache.get(exact_key)
        if cached is not None:
            logger.debug("LLM cache HIT (exact): %.60s", text)
            return cached

        # Tier 2: Semantic match
        result = classifier_result
        if result is None:
            try:
                result = self.classifier.classify(text.strip())
            except Exception:
                logger.debug("Classifier failed in LLMResponseCache.get", exc_info=True)
                return None

        sem_key = self._semantic_key(text, result)
        if sem_key is not None:
            cached = await self._cache.get(sem_key)
            if cached is not None:
                logger.debug("LLM cache HIT (semantic): %.60s → %s", text, sem_key)
                return cached

        logger.debug("LLM cache MISS: %.60s", text)
        return None

    async def set(
        self,
        text: str,
        response: str,
        classifier_result: dict[str, bool] | None = None,
    ) -> None:
        """Закэшировать ответ LLM.

        Сохраняет и exact-ключ, и semantic-ключ (если применимо).
        TTL зависит от категории запроса.

        Args:
            text: Текст сообщения пользователя.
            response: Ответ LLM для кэширования.
            classifier_result: Опциональный результат классификации.
        """
        if not settings.response_cache_enabled:
            return

        if not text or not text.strip() or not response or not response.strip():
            return

        result = classifier_result
        if result is None:
            try:
                result = self.classifier.classify(text.strip())
            except Exception:
                logger.debug("Classifier failed in LLMResponseCache.set", exc_info=True)
                return

        if not self.should_cache(text, result):
            logger.debug("LLM cache SKIP (not cacheable): %.60s", text)
            return

        ttl = self._ttl_for(result)

        # Exact key
        exact_key = self._exact_key(text)
        await self._cache.set(exact_key, response, ttl=float(ttl))

        # Semantic key
        sem_key = self._semantic_key(text, result)
        if sem_key is not None:
            await self._cache.set(sem_key, response, ttl=float(ttl))

        logger.debug(
            "LLM cache SET: %.60s (exact=%s, sem=%s, ttl=%ds)",
            text,
            exact_key,
            sem_key,
            ttl,
        )

    def should_cache(self, text: str, classifier_result: dict[str, bool]) -> bool:
        """Определить, можно ли кэшировать запрос этого типа.

        **НЕ кэшируем:**
        - Ссылки на прошлое («ты говорил», «вчера», «помнишь»)
        - @упоминания или конкретные имена
        - Команды (начинаются с /)
        - URL в тексте
        - Сложные многосоставные вопросы (>2 предложений И >15 слов)

        **Кэшируем:**
        - Приветствия, прощания
        - Простые вопросы (<10 слов, 1 предложение)
        - Общие факты/вопросы
        - Согласие/несогласие
        - Благодарность
        - Эмоциональные реакции
        """
        if not text or not text.strip():
            return False

        text_lower = text.lower().strip()

        # Команды
        if text_lower.startswith("/"):
            return False

        # @упоминания
        if "@" in text:
            return False

        # URL
        if _RE_URL.search(text):
            return False

        # Ссылки на прошлое
        for marker in _CONTEXT_DEPENDENT_MARKERS:
            if marker in text_lower:
                return False

        # Проверка на даты в тексте (контекстная зависимость)
        for date_re in _RE_DATE_PATTERNS:
            if date_re.search(text_lower):
                return False

        # Тривиальные категории — всегда кэшируем
        trivial_cats = {
            "greeting",
            "farewell",
            "agreement",
            "disagreement",
            "gratitude",
            "emotion",
            "trivial",
        }
        if any(classifier_result.get(cat, False) for cat in trivial_cats):
            return True

        # Вопросы: кэшируем только простые (1 предложение, <10 слов)
        if classifier_result.get("question", False):
            sentences = self._count_sentences(text)
            words = len(text.split())
            if sentences <= 1 and words < 10:
                return True

        # Сложные: >2 предложений И >15 слов → не кэшируем
        sentences = self._count_sentences(text)
        words = len(text.split())
        if sentences > 2 and words > 15:
            return False

        # Всё остальное: кэшируем с дефолтным TTL
        return True

    # ── Нормализация ──────────────────────────────────────────────────

    def _normalize(self, text: str) -> str:
        """Нормализовать текст для семантического сравнения.

        Шаги:
        - lowercase
        - замена URL на {URL} (ДО удаления пунктуации, иначе URL ломается)
        - удаление пунктуации (ДО замен, чтобы {NAME} и др. не пострадали)
        - замена дат на {DATE}
        - замена чисел на {NUM}
        - замена имён на {NAME}
        - нормализация пробелов

        Args:
            text: Исходный текст.

        Returns:
            Нормализованная строка.
        """
        normalized = text.lower().strip()

        # URL → {URL} (ДО удаления пунктуации, чтобы сохранить https://...)
        normalized = _RE_URL.sub("{URL}", normalized)

        # Удаление пунктуации (исключая {}, чтобы не задеть {URL} и др.)
        normalized = re.sub(r"[,.!?;:()\[\]\"'«»]+", " ", normalized)

        # Даты → {DATE}
        for date_re in _RE_DATE_PATTERNS:
            normalized = date_re.sub("{DATE}", normalized)

        # Числа → {NUM}
        normalized = _RE_NUMBER.sub("{NUM}", normalized)

        # Имена → {NAME}
        words = normalized.split()
        result_words: list[str] = []
        for word in words:
            if word in _COMMON_RUSSIAN_NAMES:
                result_words.append("{NAME}")
            else:
                result_words.append(word)
        normalized = " ".join(result_words)

        # Нормализация пробелов
        normalized = re.sub(r"\s+", " ", normalized).strip()

        return normalized

    # ── Internal helpers ───────────────────────────────────────────────

    @staticmethod
    def _exact_key(text: str) -> str:
        """Вычислить exact-match ключ: sha256 первых 200 символов."""
        digest = hashlib.sha256(text[:200].lower().strip().encode("utf-8")).hexdigest()
        return f"exact:{digest}"

    def _semantic_key(self, text: str, result: dict[str, bool]) -> str | None:
        """Построить semantic-ключ на основе категории и нормализованной структуры.

        - Тривиальные категории (greeting, farewell, ...) → blanket-ключ
        - Вопросы → категория + хэш нормализованного текста
        - Остальное → None (только exact match)

        Returns:
            Ключ семантического кэша или None.
        """
        for cat in _BLANKET_CATEGORIES:
            if result.get(cat, False):
                return f"sem:{cat}"

        for cat in _NORMALIZED_CATEGORIES:
            if result.get(cat, False):
                normalized = self._normalize(text)
                h = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
                return f"sem:{cat}:{h}"

        return None

    @staticmethod
    def _ttl_for(result: dict[str, bool]) -> int:
        """Определить TTL на основе категорий классификации."""
        if result.get("greeting") or result.get("farewell"):
            return _TTL_GREETING
        if result.get("question"):
            return _TTL_QUESTION
        if any(result.get(cat, False) for cat in _BLANKET_CATEGORIES):
            return _TTL_TRIVIAL
        return _TTL_DEFAULT

    @staticmethod
    def _count_sentences(text: str) -> int:
        """Подсчитать количество предложений в тексте."""
        # Считаем разделители: . ! ? + переводы строк как границы предложений
        count = len(re.findall(r"[.!?]+", text))
        # Также считаем переводы строк как границы
        count += text.count("\n")
        return max(count, 1)

    # ── Stats ──────────────────────────────────────────────────────────

    async def stats(self) -> dict[str, Any]:
        """Получить статистику кэша."""
        cache_stats = await self._cache.stats()
        return {
            "llm_response_cache": cache_stats,
            "response_cache_enabled": settings.response_cache_enabled,
        }

    async def reset_for_test(self) -> None:
        """Сбросить кэш для тестов."""
        await self._cache.clear()


# ── Factory & singleton ────────────────────────────────────────────────


def create_response_cache() -> LLMResponseCache:
    """Создать новый экземпляр :class:`LLMResponseCache`.

    Для внедрения зависимостей (тесты, кастомный жизненный цикл).
    """
    return LLMResponseCache()


def _reset_response_cache_for_test() -> LLMResponseCache:
    """Заменить глобальный синглтон свежим экземпляром (для тестов).

    Использует актуальный ``cache_manager`` из ``src.core.cache.manager``,
    чтобы избежать проблем с устаревшей ссылкой после
    :func:`_reset_cache_manager_for_test`.
    """
    import src.core.cache.manager as _cm_mod

    new_rc = LLMResponseCache(cm=_cm_mod.cache_manager)
    import src.core.intelligence.llm_response_cache as _mod

    _mod.response_cache = new_rc
    return new_rc


# Глобальный синглтон
response_cache = LLMResponseCache()
