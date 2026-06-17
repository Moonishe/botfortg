"""Сервис мониторинга объявлений Авито.

Оркестрирует:
- Построение URL поиска
- Загрузка страницы (stealth-сессия с антидетектом)
- Парсинг объявлений
- Оценка выгодности (deal_score)
- Проверка на мошенничество (anti_scam)
- Сравнение с БД (инкрементальный анализ)
- Загрузка полных описаний с карточек (опционально)
- LLM-анализ объявлений (опционально)
- Ротация прокси (опционально)
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time as _time_module
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote_plus

from src.core.avito.anti_scam import check_scam
from src.core.avito.deal_score import calculate_deal_score
from src.core.avito.parser import parse_listings

# ── SSRF-защита: допустимые домены Авито ─────────────────────────────────
_ALLOWED_AVITO_HOSTS = {"www.avito.ru", "m.avito.ru", "avito.ru"}


def _validate_avito_url(url: str) -> str:
    """Валидирует что URL указывает на домен Авито перед HTTP-запросом.

    Предотвращает SSRF-атаки — запрещает запросы к другим доменам.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.hostname not in _ALLOWED_AVITO_HOSTS and not (
        parsed.hostname and parsed.hostname.endswith(".avito.ru")
    ):
        raise ValueError(
            f"URL not allowed: {parsed.hostname} (expected avito.ru domain)"
        )
    return url


logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  Типы
# ═══════════════════════════════════════════════════════════════════════════


class SearchParams:
    """Параметры поиска на Авито."""

    def __init__(
        self,
        city: str,
        category: str,
        query: str,
        *,
        price_min: int | None = None,
        price_max: int | None = None,
    ) -> None:
        self.city = city
        self.category = category
        self.query = query
        self.price_min = price_min
        self.price_max = price_max


class ScanResult:
    """Результат сканирования."""

    def __init__(self) -> None:
        self.listings: list[dict[str, Any]] = []
        self.new_listings: list[dict[str, Any]] = []
        self.price_changes: list[dict[str, Any]] = []
        self.unchanged: list[dict[str, Any]] = []
        self.error: str | None = None
        self.url: str = ""
        self.total_parsed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "listings": self.listings,
            "new_listings": self.new_listings,
            "price_changes": self.price_changes,
            "unchanged": self.unchanged,
            "error": self.error,
            "url": self.url,
            "total_parsed": self.total_parsed,
        }


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP-загрузка (stealth-сессия) + прокси
# ═══════════════════════════════════════════════════════════════════════════

if TYPE_CHECKING:
    from src.core.avito.stealth.session import AvitoSession

_stealth_session: AvitoSession | None = None
_stealth_lock = asyncio.Lock()
_proxy_rotator: object | None = None
_proxy_rotator_checked: bool = False  # True = already checked, configured or not
_proxy_rotator_lock = asyncio.Lock()


async def _get_proxy_rotator():
    """Lazy-init ротатора прокси (если настроен)."""
    global _proxy_rotator, _proxy_rotator_checked

    if _proxy_rotator is not None or _proxy_rotator_checked:
        return _proxy_rotator

    async with _proxy_rotator_lock:
        if _proxy_rotator is not None or _proxy_rotator_checked:
            return _proxy_rotator

        _proxy_rotator_checked = True
        try:
            from src.config import settings

            proxy_list_raw = settings.avito_proxy_list
            if not proxy_list_raw or not proxy_list_raw.strip():
                return None

            proxies = json.loads(proxy_list_raw)
            if not proxies or not isinstance(proxies, list):
                return None

            from src.core.avito.proxy_rotator import ProxyRotator

            _proxy_rotator = ProxyRotator(proxies)
            return _proxy_rotator
        except Exception:
            logger.debug(
                "_get_proxy_rotator: не удалось инициализировать", exc_info=True
            )
            return None


async def shutdown_avito_rotator(timeout: float = 10.0) -> None:
    """Gracefully drain pending proxy rotation tasks if rotator is initialized."""
    global _proxy_rotator
    if _proxy_rotator is None:
        return
    if TYPE_CHECKING:
        from src.core.avito.proxy_rotator import ProxyRotator
    _proxy_rotator = cast(ProxyRotator, _proxy_rotator)
    try:
        await _proxy_rotator.shutdown(timeout=timeout)
    except Exception:
        logger.debug("Avito rotator shutdown failed (non-critical)", exc_info=True)
    _proxy_rotator = None


async def _get_stealth_session(proxy_url: str | None = None):
    """Lazy-init the stealth session (warmup once, reuse).

    If proxy_url changes — atomically close old session and create new one
    under a single lock acquisition to prevent TOCTOU races.

    NOTE: Callers holding a reference to the session while another caller
    triggers a proxy change may see their in-flight request fail. This is
    a fundamental limitation of the global singleton pattern — it assumes
    proxy changes are rare (ProxyRotator throttles failures) and that
    failed requests will be retried by the caller.
    """
    global _stealth_session

    if proxy_url is not None and _stealth_session is not None:
        async with _stealth_lock:
            # Re-check inside lock: another caller may have already
            # recreated the session with the same proxy_url.
            if _stealth_session is not None:
                current_proxy = getattr(_stealth_session, "proxy", None)
                if current_proxy == proxy_url:
                    return _stealth_session  # already correct proxy
                try:
                    await _stealth_session.close()  # type: ignore[attr-defined]
                except Exception:
                    logger.debug("Non-critical error", exc_info=True)
                _stealth_session = None
            # Create new session with the requested proxy
            from src.core.avito.stealth.session import AvitoSession

            _stealth_session = AvitoSession(proxy=proxy_url)
            await _stealth_session.warmup()  # type: ignore[attr-defined]
        return _stealth_session

    if _stealth_session is None:
        async with _stealth_lock:
            if _stealth_session is None:
                from src.core.avito.stealth.session import AvitoSession

                _stealth_session = AvitoSession(proxy=proxy_url)
                await _stealth_session.warmup()  # type: ignore[attr-defined]
    return _stealth_session


async def _close_stealth_session() -> None:
    """Закрывает глобальную stealth-сессию.

    M3: закрытие под _stealth_lock — предотвращает двойное закрытие
    параллельными вызовами и гонку с _get_stealth_session().
    """
    global _stealth_session
    async with _stealth_lock:
        if _stealth_session is not None:
            try:
                await _stealth_session.close()  # type: ignore[attr-defined]
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
            _stealth_session = None


async def _fetch_page(url: str) -> str:
    """Загружает HTML-страницу через stealth-сессию (httpx + browser fallback).

    Если настроен ProxyRotator — использует прокси из пула.
    """
    # SSRF-защита: defence in depth
    _validate_avito_url(url)

    # Получаем прокси если настроен ротатор
    proxy_url: str | None = None
    proxy_entry: object | None = None

    rotator = await _get_proxy_rotator()
    if rotator is not None:
        proxy_entry = await rotator.get_proxy()  # type: ignore[attr-defined]
        if proxy_entry is not None:
            proxy_url = proxy_entry.url  # type: ignore[attr-defined]

    session = await _get_stealth_session(proxy_url=proxy_url)
    try:
        resp = await session.fetch(url)  # type: ignore[attr-defined]
        if resp.status_code != 200:
            # Отмечаем ошибку прокси если был использован
            if proxy_entry is not None and rotator is not None:
                try:
                    await rotator.mark_failure(proxy_entry)  # type: ignore[attr-defined]
                except Exception:
                    logger.debug("Non-critical error", exc_info=True)
            raise RuntimeError(f"HTTP {resp.status_code}: страница не загружена")

        # Успех — сбрасываем счётчик ошибок прокси
        if proxy_entry is not None and rotator is not None:
            try:
                await rotator.mark_success(proxy_entry)  # type: ignore[attr-defined]
            except Exception:
                logger.debug("Non-critical error", exc_info=True)

        return resp.text
    except RuntimeError:
        raise
    except Exception:
        # Отмечаем ошибку прокси
        if proxy_entry is not None and rotator is not None:
            try:
                await rotator.mark_failure(proxy_entry)  # type: ignore[attr-defined]
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
        raise


# ═══════════════════════════════════════════════════════════════════════════
#  Построение URL
# ═══════════════════════════════════════════════════════════════════════════


def build_avito_url(params: SearchParams) -> str:
    """Формирует URL поиска Авито.

    Формат: https://www.avito.ru/{city}/{category}?q={query}&pmin={min}&pmax={max}
    """
    city = params.city.strip().lower().replace(" ", "_")
    category = params.category.strip().lower().replace(" ", "_")
    query = params.query.strip()
    if not city:
        raise ValueError("Avito city is required")
    if not query:
        raise ValueError("Avito query is required")
    query_encoded = quote_plus(query)

    if category:
        url = f"https://www.avito.ru/{city}/{category}?q={query_encoded}"
    else:
        url = f"https://www.avito.ru/{city}?q={query_encoded}"

    if params.price_min is not None:
        url += f"&pmin={params.price_min}"
    if params.price_max is not None:
        url += f"&pmax={params.price_max}"

    return url


# ═══════════════════════════════════════════════════════════════════════════
#  Рыночная статистика
# ═══════════════════════════════════════════════════════════════════════════


def _calc_market_stats(listings: list[dict[str, Any]]) -> dict[str, float | None]:
    """Рассчитывает рыночную статистику (средняя, минимальная цена)."""
    prices = [item["price"] for item in listings if item.get("price") is not None]
    if not prices:
        return {"avg_price": None, "min_price": None}
    return {
        "avg_price": sum(prices) / len(prices),
        "min_price": float(min(prices)),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Инкрементальный анализ
# ═══════════════════════════════════════════════════════════════════════════


def _compare_with_db(
    parsed: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Сравнивает спарсенные объявления с существующими в БД.

    Args:
        parsed: Список спарсенных объявлений.
        existing: Словарь {avito_id: listing_data} из БД (или None).

    Returns:
        (new_listings, price_changes, unchanged)
    """
    if existing is None:
        return parsed, [], []

    new_listings: list[dict[str, Any]] = []
    price_changes: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []

    for listing in parsed:
        avito_id = listing.get("avito_id")
        if not avito_id:
            new_listings.append(listing)
            continue

        old = existing.get(avito_id)
        if old is None:
            new_listings.append(listing)
            continue

        old_price = old.get("price")
        new_price = listing.get("price")

        if old_price is not None and new_price is not None and old_price != new_price:
            listing["previous_price"] = old_price
            price_changes.append(listing)
        else:
            unchanged.append(listing)

    return new_listings, price_changes, unchanged


# ═══════════════════════════════════════════════════════════════════════════
#  Кэш результатов сканирования
# ═══════════════════════════════════════════════════════════════════════════

_SCAN_CACHE: dict[str, tuple[float, ScanResult]] = {}
_SCAN_CACHE_TTL = 300  # 5 минут
_SCAN_CACHE_MAX_SIZE = 500
_SCAN_CACHE_LOCK = asyncio.Lock()
_SCAN_IN_FLIGHT: dict[str, asyncio.Event] = {}

# ═══════════════════════════════════════════════════════════════════════════
#  Глобальные ограничители concurrency
# ═══════════════════════════════════════════════════════════════════════════

_LLM_ANALYSIS_SEM = asyncio.Semaphore(3)


def _cache_hash(params: SearchParams) -> str:
    return hashlib.sha256(
        f"{params.city}:{params.query}:{params.price_min}:{params.price_max}".encode()
    ).hexdigest()


async def scan_avito_cached(params: SearchParams) -> ScanResult:
    """scan_avito с кэшированием результата на 5 минут.

    Дедупликация in-flight запросов: параллельные вызовы с одинаковыми
    params ждут завершения первого запроса вместо повторного HTTP+парсинга.
    """
    key = _cache_hash(params)
    now = _time_module.time()

    # Проверка кэша под блокировкой (защита от TOCTOU)
    async with _SCAN_CACHE_LOCK:
        if key in _SCAN_CACHE:
            ts, result = _SCAN_CACHE[key]
            if now - ts < _SCAN_CACHE_TTL:
                return copy.deepcopy(result)
        # Регистрируем in-flight запрос
        if key not in _SCAN_IN_FLIGHT:
            _SCAN_IN_FLIGHT[key] = asyncio.Event()
            owner = True
        else:
            owner = False
            event = _SCAN_IN_FLIGHT[key]

    if not owner:
        # Ждём завершения in-flight запроса
        await event.wait()
        async with _SCAN_CACHE_LOCK:
            if key in _SCAN_CACHE:
                ts, result = _SCAN_CACHE[key]
                if now - ts < _SCAN_CACHE_TTL:
                    return copy.deepcopy(result)
        # Если кэш не появился (редкий race), выполняем самостоятельно

    # Тяжёлая операция — выполняем БЕЗ блокировки
    try:
        result = await scan_avito(params)
    finally:
        async with _SCAN_CACHE_LOCK:
            event = _SCAN_IN_FLIGHT.pop(key, None)
            if event is not None:
                event.set()

    # Запись результата и очистка под блокировкой
    async with _SCAN_CACHE_LOCK:
        _SCAN_CACHE[key] = (now, result)
        # Очистка старых записей
        if len(_SCAN_CACHE) > _SCAN_CACHE_MAX_SIZE:
            # Evict oldest half by timestamp
            sorted_items = sorted(_SCAN_CACHE.items(), key=lambda item: item[1][0])
            for k, _ in sorted_items[: len(sorted_items) // 2]:
                del _SCAN_CACHE[k]
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  Публичный API
# ═══════════════════════════════════════════════════════════════════════════


async def scan_avito(
    params: SearchParams,
    *,
    existing: dict[str, dict[str, Any]] | None = None,
    fetch_details: bool = False,  # NEW: загружать полные описания с карточек
    detail_fetch_limit: int = 10,  # NEW: макс. карточек для обогащения (top-N)
) -> ScanResult:
    """Полный цикл сканирования Авито.

    1. Строит URL
    2. Загружает страницу
    3. Парсит объявления
    4. Считает deal_score для каждого
    5. Проверяет на мошенничество
    6. Сравнивает с existing (инкрементальный анализ)
    7. (NEW) Если fetch_details=True — загружает полные описания
    8. (NEW) Если avito_llm_analysis=True — анализирует через LLM

    Args:
        params: Параметры поиска.
        existing: Словарь {avito_id: listing_data} из БД для инкрементального анализа.
        fetch_details: Загружать полные описания с карточек объявлений.
        detail_fetch_limit: Максимум карточек для загрузки полных описаний.

    Returns:
        ScanResult с полными данными.
    """
    result = ScanResult()

    # 1. URL
    url = build_avito_url(params)
    result.url = url
    logger.info("scan_avito: загрузка %s", url)

    # 2. Загрузка
    try:
        html = await _fetch_page(url)
    except RuntimeError as exc:
        result.error = str(exc)
        logger.error("scan_avito: ошибка загрузки — %s", exc)
        return result
    except TimeoutError:
        result.error = "Таймаут загрузки страницы"
        logger.error("scan_avito: timeout")
        return result
    except OSError:
        result.error = "Не удалось подключиться к avito.ru"
        logger.error("scan_avito: connection error", exc_info=True)
        return result
    except Exception:
        result.error = "Неизвестная ошибка загрузки"
        logger.exception("scan_avito: неизвестная ошибка")
        return result

    # 3. Парсинг
    try:
        parsed = parse_listings(html)
    except Exception:
        result.error = "Ошибка парсинга HTML"
        logger.exception("scan_avito: ошибка парсинга")
        return result

    result.total_parsed = len(parsed)

    if not parsed:
        result.error = "Объявления не найдены"
        logger.info("scan_avito: 0 объявлений на странице")
        return result

    # 4. Рыночная статистика
    stats = _calc_market_stats(parsed)

    # 5. Оценка и проверка каждого объявления
    for listing in parsed:
        try:
            deal = calculate_deal_score(
                listing,
                avg_price=stats["avg_price"],
                min_price=stats["min_price"],
            )
            listing["deal_score"] = deal
        except Exception:
            logger.exception(
                "scan_avito: ошибка deal_score для %s", listing.get("avito_id")
            )
            listing["deal_score"] = {"score": 0, "breakdown": {}, "grade": "F"}

        try:
            scam = check_scam(listing, avg_price=stats["avg_price"])
            listing["scam_check"] = scam
        except Exception:
            logger.exception(
                "scan_avito: ошибка anti_scam для %s", listing.get("avito_id")
            )
            listing["scam_check"] = {
                "is_suspicious": False,
                "risk": "low",
                "reasons": [],
            }

    # ── (NEW) 6. Обогащение полными описаниями ───────────────────────────
    if fetch_details and parsed:
        await _enrich_listings(parsed, detail_fetch_limit)

        # Пересчитываем deal_score с полным описанием
        for listing in parsed:
            full_desc = listing.get("full_description", "")
            if full_desc:
                # Подменяем description на полное для скоринга
                original_desc = listing.get("description", "")
                listing["description"] = full_desc
                try:
                    deal = calculate_deal_score(
                        listing,
                        avg_price=stats["avg_price"],
                        min_price=stats["min_price"],
                    )
                    listing["deal_score"] = deal
                except Exception:
                    logger.exception(
                        "scan_avito: ошибка пересчёта deal_score для %s",
                        listing.get("avito_id"),
                    )
                # Восстанавливаем оригинальное короткое описание
                listing["description"] = original_desc

    # ── (NEW) 7. LLM-анализ ──────────────────────────────────────────────
    try:
        from src.config import settings

        if settings.avito_llm_analysis:
            await _llm_analyze_listings(parsed, detail_fetch_limit)
    except Exception:
        logger.exception("scan_avito: ошибка LLM-анализа")

    result.listings = parsed

    # 8. Инкрементальный анализ
    new_listings, price_changes, unchanged = _compare_with_db(parsed, existing)
    result.new_listings = new_listings
    result.price_changes = price_changes
    result.unchanged = unchanged

    logger.info(
        "scan_avito: всего=%d, новых=%d, цен=%d, без изменений=%d",
        len(parsed),
        len(new_listings),
        len(price_changes),
        len(unchanged),
    )

    return result


# ═══════════════════════════════════════════════════════════════════════════
#  Обогащение объявлений (полные описания)
# ═══════════════════════════════════════════════════════════════════════════


async def _enrich_listings(
    parsed: list[dict[str, Any]],
    limit: int,
) -> None:
    """Загружает полные описания для top-N объявлений по deal_score."""
    if not parsed or limit <= 0:
        return

    # Сортируем по deal_score (лучшие первые)
    sorted_listings = sorted(
        parsed,
        key=lambda x: x.get("deal_score", {}).get("score", 0),
        reverse=True,
    )

    # Берём top-N с URL
    to_fetch = [
        (listing, listing["url"])
        for listing in sorted_listings[:limit]
        if listing.get("url")
    ]

    if not to_fetch:
        return

    urls = [url for _, url in to_fetch]
    logger.info(
        "_enrich_listings: загрузка %d карточек из %d объявлений",
        len(urls),
        len(parsed),
    )

    # Используем существующую сессию (или передаём None — batch создаст свою)
    try:
        stealth = await _get_stealth_session()
    except Exception:
        logger.exception("_enrich_listings: не удалось получить stealth-сессию")
        return

    try:
        from src.core.avito.listing_fetcher import fetch_listing_details_batch

        # _get_stealth_session returns AvitoSession at runtime; the global is
        # typed as object to avoid circular imports. fetch_listing_details_batch
        # handles None by creating its own session.
        details = await fetch_listing_details_batch(
            urls,
            session=stealth if stealth is not None else None,  # type: ignore[arg-type]
            concurrency=3,
        )
    except Exception:
        logger.exception("_enrich_listings: ошибка загрузки деталей")
        return

    # Обновляем объявления
    for listing, url in to_fetch:
        detail = details.get(url)
        if detail is None or detail.get("error"):
            listing["_detail_error"] = detail.get("error") if detail else "no_data"
            continue

        # Сливаем данные
        listing["full_description"] = detail.get("full_description", "")
        listing["view_count"] = detail.get("view_count")
        listing["extra_images"] = detail.get("extra_images", [])
        listing["seller_joined_date"] = detail.get("seller_joined_date")
        listing["listing_characteristics"] = detail.get("listing_characteristics", {})
        listing["seller_other_listings_count"] = detail.get(
            "seller_other_listings_count"
        )
        listing["_detail_error"] = None

    enriched = sum(
        1
        for listing, _ in to_fetch
        if listing.get("full_description") and not listing.get("_detail_error")
    )
    logger.info("_enrich_listings: обогащено %d/%d", enriched, len(to_fetch))


async def _llm_analyze_listings(
    parsed: list[dict[str, Any]],
    limit: int,
) -> None:
    """Анализирует top-N объявлений через LLM (параллельно с семафором)."""
    if not parsed or limit <= 0:
        return

    # Берём top-N по deal_score с полным описанием
    candidates = [
        listing
        for listing in parsed
        if listing.get("full_description") or listing.get("description")
    ]
    candidates.sort(
        key=lambda x: x.get("deal_score", {}).get("score", 0),
        reverse=True,
    )
    to_analyze = candidates[:limit]

    if not to_analyze:
        return

    logger.info(
        "_llm_analyze_listings: анализ %d объявлений через LLM", len(to_analyze)
    )

    from src.core.avito.llm_analyzer import analyze_listing_llm

    # ── Параллельный LLM-анализ с глобальным семафором (макс. 3 вызова) ──
    async def _analyze_one(listing: dict[str, Any]) -> None:
        """Анализирует одно объявление через LLM (с обработкой ошибок)."""
        async with _LLM_ANALYSIS_SEM:
            try:
                analysis = await analyze_listing_llm(listing)
                listing["llm_analysis"] = analysis
            except Exception:
                logger.exception(
                    "_llm_analyze_listings: ошибка для %s", listing.get("avito_id")
                )
                listing["llm_analysis"] = {
                    "deal_quality": 0,
                    "red_flags": [],
                    "recommendation": "skip",
                    "summary": "",
                    "reasoning": "",
                    "error": "Ошибка анализа",
                }

    await asyncio.gather(
        *[_analyze_one(listing) for listing in to_analyze],
        return_exceptions=True,
    )



