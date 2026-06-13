"""Загрузчик страниц объявлений Авито.

Парсит индивидуальные карточки объявлений для получения:
- Полного описания (в отличие от краткого в поисковой выдаче)
- Количества просмотров
- Дополнительных изображений
- Даты регистрации продавца
- Характеристик товара (параметры)
- Количества других объявлений продавца
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, TYPE_CHECKING

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from src.core.avito.stealth.session import AvitoSession

logger = logging.getLogger(__name__)

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


# ═══════════════════════════════════════════════════════════════════════════
#  Константы
# ═══════════════════════════════════════════════════════════════════════════

# Селекторы для ключевых элементов карточки объявления
_SELECTOR_DESCRIPTION = [
    '[data-marker="item-view/item-description"]',
    'div[class*="item-description"]',
    'div[class*="description"]',
]

_SELECTOR_VIEWS = [
    '[data-marker="item-view/title-info"]',
    'span[data-marker*="views"]',
    'span[data-marker*="item-view"]',
]

_SELECTOR_CHARACTERISTICS = [
    'ul[data-marker="item-view/item-params"] li',
    'ul[class*="item-params"] li',
    'li[class*="params-paramsList"]',
]

_SELECTOR_SELLER_BLOCK = [
    '[data-marker="seller-info"]',
    'div[class*="seller-info"]',
    'div[class*="seller"]',
]

_SELECTOR_IMAGES = [
    'ul[data-marker="image-frame/image-wrapper"] img',
    'div[class*="image-frame"] img',
    'div[class*="gallery"] img',
]

_SELECTOR_OTHER_LISTINGS = [
    '[data-marker="seller-link"]',
    'a[class*="seller-link"]',
]

# Паттерны для извлечения данных из текста
_RE_VIEWS = re.compile(r"(\d[\d\s]*)\s*просмотр", re.IGNORECASE)
_RE_SELLER_JOINED = re.compile(r"на\s+Авито\s+с\s+(.+?)(?:$|\.|,)", re.IGNORECASE)
_RE_NUMBERS = re.compile(r"(\d[\d\s]*)")


# ═══════════════════════════════════════════════════════════════════════════
#  Утилиты
# ═══════════════════════════════════════════════════════════════════════════


def _clean(text: str | None) -> str:
    """Убирает лишние пробелы и неразрывные символы."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def _extract_number(text: str) -> int | None:
    """Извлекает первое число из текста."""
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  Функции извлечения данных
# ═══════════════════════════════════════════════════════════════════════════


def _extract_full_description(soup: BeautifulSoup) -> str:
    """Извлекает полное описание из карточки объявления."""
    for selector in _SELECTOR_DESCRIPTION:
        el = soup.select_one(selector)
        if el:
            text = _clean(el.get_text())
            if len(text) > 20:  # Минимальная осмысленная длина
                return text

    # Fallback: ищем большой текстовый блок в основном контенте
    main = soup.select_one('div[data-marker="item-view"]')
    if main:
        # Ищем div с классом содержащим "description" или "text"
        for div in main.select("div"):
            cls = " ".join(str(c) for c in (div.get("class") or []))
            if "description" in cls.lower():
                text = _clean(div.get_text())
                if len(text) > 20:
                    return text

    return ""


def _extract_view_count(soup: BeautifulSoup) -> int | None:
    """Извлекает количество просмотров."""
    # Ищем текст с "просмотров" в любом элементе
    for el in soup.select("span"):
        text = _clean(el.get_text())
        m = _RE_VIEWS.search(text)
        if m:
            return _extract_number(m.group(1))

    # Пробуем data-marker с views
    for selector in _SELECTOR_VIEWS:
        el = soup.select_one(selector)
        if el:
            text = _clean(el.get_text())
            m = _RE_VIEWS.search(text)
            if m:
                return _extract_number(m.group(1))

    return None


def _extract_characteristics(soup: BeautifulSoup) -> dict[str, str]:
    """Извлекает характеристики товара (параметры)."""
    result: dict[str, str] = {}

    for selector in _SELECTOR_CHARACTERISTICS:
        items = soup.select(selector)
        if items:
            for li in items:
                text = _clean(li.get_text())
                # Формат: «Параметр: значение» или «Параметр значение»
                if ":" in text:
                    parts = text.split(":", 1)
                    key = _clean(parts[0])
                    val = _clean(parts[1]) if len(parts) > 1 else ""
                else:
                    # Ищем два span внутри li
                    spans = li.select("span")
                    if len(spans) >= 2:
                        key = _clean(spans[0].get_text())
                        val = _clean(spans[1].get_text())
                    else:
                        continue

                if key and len(key) < 100:  # Разумный ключ
                    result[key] = val

            if result:
                break

    return result


def _extract_seller_joined_date(soup: BeautifulSoup) -> str | None:
    """Извлекает дату регистрации продавца на Авито."""
    for selector in _SELECTOR_SELLER_BLOCK:
        seller = soup.select_one(selector)
        if seller:
            text = _clean(seller.get_text())
            m = _RE_SELLER_JOINED.search(text)
            if m:
                return _clean(m.group(1))

    # Fallback: ищем "на Авито с" во всём документе
    text = soup.get_text()
    m = _RE_SELLER_JOINED.search(text)
    if m:
        return _clean(m.group(1))
    return None


def _extract_extra_images(soup: BeautifulSoup) -> list[str]:
    """Извлекает дополнительные изображения из галереи."""
    images: list[str] = []

    for selector in _SELECTOR_IMAGES:
        imgs = soup.select(selector)
        for img in imgs:
            src = img.get("src") or img.get("data-src") or ""
            if src and "avito" in str(src).lower():
                images.append(str(src))

        if images:
            break

    # Убираем дубликаты
    seen: set[str] = set()
    unique: list[str] = []
    for img in images:
        if img not in seen:
            seen.add(img)
            unique.append(img)

    return unique


def _extract_seller_other_count(soup: BeautifulSoup) -> int | None:
    """Извлекает количество других объявлений продавца."""
    for selector in _SELECTOR_OTHER_LISTINGS:
        el = soup.select_one(selector)
        if el:
            text = _clean(el.get_text())
            m = _RE_NUMBERS.search(text)
            if m:
                return _extract_number(m.group(1))

    # Fallback: ищем текст типа «N объявлений» в seller-блоке
    for selector in _SELECTOR_SELLER_BLOCK:
        seller = soup.select_one(selector)
        if seller:
            text = _clean(seller.get_text())
            m = re.search(r"(\d+)\s*объявл", text, re.IGNORECASE)
            if m:
                return _extract_number(m.group(1))

    return None


# ═══════════════════════════════════════════════════════════════════════════
#  Публичный API
# ═══════════════════════════════════════════════════════════════════════════


async def fetch_listing_detail(
    url: str,
    session: AvitoSession = None,
) -> dict[str, Any]:
    """Загружает и парсит индивидуальную карточку объявления Авито.

    Args:
        url: Полный URL объявления (e.g. https://www.avito.ru/moskva/..._123456).
        session: Опционально — существующая AvitoSession (переиспользовать).

    Returns:
        dict с ключами:
            full_description, view_count, extra_images, seller_joined_date,
            listing_characteristics, seller_other_listings_count, error
    """
    result: dict[str, Any] = {
        "full_description": "",
        "view_count": None,
        "extra_images": [],
        "seller_joined_date": None,
        "listing_characteristics": {},
        "seller_other_listings_count": None,
        "error": None,
    }

    try:
        # SSRF-защита: проверяем домен
        _validate_avito_url(url)

        # Загружаем HTML через сессию
        if session is not None:
            resp = await session.fetch(url)
            if resp.status_code != 200:
                result["error"] = f"HTTP {resp.status_code}: страница не загружена"
                return result
            html = resp.text
        else:
            # Без сессии — создаём новую (lazy import)
            from src.core.avito.stealth.session import AvitoSession

            temp_session = AvitoSession()
            try:
                await temp_session.warmup()
                resp = await temp_session.fetch(url)
                if resp.status_code != 200:
                    result["error"] = f"HTTP {resp.status_code}: страница не загружена"
                    return result
                html = resp.text
            finally:
                await temp_session.close()

        # Проверка на блокировку
        if _is_blocked(html):
            result["error"] = "Страница заблокирована (captcha/ограничение)"
            logger.warning("fetch_listing_detail: блокировка для %s", url)
            return result

        # Парсинг
        soup = BeautifulSoup(html, "html.parser")

        result["full_description"] = _extract_full_description(soup)
        result["view_count"] = _extract_view_count(soup)
        result["extra_images"] = _extract_extra_images(soup)
        result["seller_joined_date"] = _extract_seller_joined_date(soup)
        result["listing_characteristics"] = _extract_characteristics(soup)
        result["seller_other_listings_count"] = _extract_seller_other_count(soup)

        logger.debug(
            "fetch_listing_detail: desc_len=%d, views=%s, chars=%d для %s",
            len(result["full_description"]),
            result["view_count"],
            len(result["listing_characteristics"]),
            url,
        )

    except TimeoutError:
        result["error"] = "Таймаут загрузки страницы"
        logger.error("fetch_listing_detail: timeout для %s", url)
    except Exception:
        result["error"] = "Ошибка загрузки/парсинга"
        logger.exception("fetch_listing_detail: ошибка для %s", url)

    return result


async def fetch_listing_details_batch(
    urls: list[str],
    session: AvitoSession = None,
    concurrency: int = 3,
) -> dict[str, dict[str, Any]]:
    """Загружает несколько карточек объявлений конкурентно.

    Args:
        urls: Список URL объявлений.
        session: Опционально — существующая AvitoSession (переиспользовать).
        concurrency: Максимальное количество одновременных запросов.

    Returns:
        dict mapping url → detail dict.
    """
    if not urls:
        return {}

    # SSRF-защита: валидируем все URL до отправки запросов
    for url in urls:
        _validate_avito_url(url)

    semaphore = asyncio.Semaphore(concurrency)

    async def _fetch_one(url: str) -> dict[str, Any]:
        async with semaphore:
            return await fetch_listing_detail(url, session=session)

    # Создаём сессию если не передана
    own_session = False
    if session is None:
        from src.core.avito.stealth.session import AvitoSession

        session = AvitoSession()
        await session.warmup()
        own_session = True

    try:
        tasks = [_fetch_one(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, dict[str, Any]] = {}
        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                output[url] = {
                    "full_description": "",
                    "view_count": None,
                    "extra_images": [],
                    "seller_joined_date": None,
                    "listing_characteristics": {},
                    "seller_other_listings_count": None,
                    "error": f"Исключение: {result}",
                }
            else:
                output[url] = result

        return output
    finally:
        if own_session:
            await session.close()


# ═══════════════════════════════════════════════════════════════════════════
#  Вспомогательные
# ═══════════════════════════════════════════════════════════════════════════


def _is_blocked(html: str) -> bool:
    """Проверяет, не заблокирована ли страница."""
    if not html or len(html) < 500:
        return True
    lower = html.lower()
    blocked_signals = (
        "captcha",
        "проверка",
        "доступ ограничен",
        "доступ запрещён",
        "слишком много запросов",
    )
    for signal in blocked_signals:
        if signal in lower:
            return True
    return False
