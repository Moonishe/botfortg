"""Тесты парсера карточек объявлений Авито (listing_fetcher)."""

import pytest
from src.core.avito.listing_fetcher import (
    _extract_full_description,
    _extract_view_count,
    _extract_characteristics,
    _extract_seller_joined_date,
    _extract_extra_images,
    _extract_seller_other_count,
    _is_blocked,
    _extract_number,
)

# ═══════════════════════════════════════════════════════════════════════════
#  HTML-образцы
# ═══════════════════════════════════════════════════════════════════════════

SAMPLE_LISTING_HTML = """
<html>
<body>
<div data-marker="item-view">
    <div data-marker="item-view/item-description">
        <div class="item-description-html">
            <p>MacBook Air 13" 2020 года. Процессор M1, 8 ГБ ОЗУ, 256 ГБ SSD.</p>
            <p>Состояние отличное, без царапин. Полный комплект: коробка, зарядка, документы.</p>
            <p>Причина продажи — купил новый MacBook Pro. Торг уместен.</p>
        </div>
    </div>

    <span data-marker="item-view/title-info">
        • 2 345 просмотров • сегодня в 12:34
    </span>

    <ul data-marker="item-view/item-params">
        <li><span>Тип процессора</span><span>Apple M1</span></li>
        <li><span>Объем оперативной памяти</span><span>8 ГБ</span></li>
        <li><span>Диагональ экрана</span><span>13.3"</span></li>
        <li><span>Объем SSD</span><span>256 ГБ</span></li>
    </ul>

    <div data-marker="seller-info">
        <div>
            <span>Алексей</span>
            <span>★★★★★ 4.8</span>
            <span>42 отзыва</span>
            <span>на Авито с марта 2019</span>
        </div>
        <a data-marker="seller-link">12 объявлений пользователя</a>
    </div>

    <ul data-marker="image-frame/image-wrapper">
        <li><img src="https://20.img.avito.st/image/1.jpg"/></li>
        <li><img src="https://30.img.avito.st/image/2.jpg"/></li>
        <li><img src="https://40.img.avito.st/image/3.jpg"/></li>
    </ul>
</div>
</body>
</html>
"""

EMPTY_LISTING_HTML = """
<html><body><div>Ничего нет</div></body></html>
"""

BLOCKED_PAGE_HTML = """
<html><body><h1>Доступ ограничен</h1><p>Пожалуйста, пройдите проверку</p></body></html>
"""

CAPTCHA_PAGE_HTML = """
<html><body><div>captcha required</div></body></html>
"""

SHORT_HTML = """<html></html>"""  # меньше 500 символов — считается blocked


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: утилиты
# ═══════════════════════════════════════════════════════════════════════════


def test_extract_number() -> None:
    assert _extract_number("2 345") == 2345
    assert _extract_number("42 отзыва") == 42
    assert _extract_number("нет чисел") is None
    assert _extract_number("") is None


def test_is_blocked() -> None:
    assert _is_blocked(BLOCKED_PAGE_HTML)
    assert _is_blocked(CAPTCHA_PAGE_HTML)
    assert _is_blocked(SHORT_HTML)
    assert _is_blocked("")
    assert not _is_blocked(SAMPLE_LISTING_HTML)


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: извлечение полей
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def soup():
    from bs4 import BeautifulSoup

    return BeautifulSoup(SAMPLE_LISTING_HTML, "html.parser")


def test_extract_full_description(soup) -> None:
    desc = _extract_full_description(soup)
    assert len(desc) > 100
    assert "MacBook Air" in desc
    assert "M1" in desc
    assert "полный комплект" in desc.lower()


def test_extract_full_description_empty() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(EMPTY_LISTING_HTML, "html.parser")
    desc = _extract_full_description(soup)
    assert desc == ""


def test_extract_view_count(soup) -> None:
    views = _extract_view_count(soup)
    assert views == 2345


def test_extract_view_count_empty() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(EMPTY_LISTING_HTML, "html.parser")
    views = _extract_view_count(soup)
    assert views is None


def test_extract_characteristics(soup) -> None:
    chars = _extract_characteristics(soup)
    assert len(chars) == 4
    assert chars["Тип процессора"] == "Apple M1"
    assert chars["Объем оперативной памяти"] == "8 ГБ"
    assert chars["Диагональ экрана"] == '13.3"'
    assert chars["Объем SSD"] == "256 ГБ"


def test_extract_characteristics_empty() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(EMPTY_LISTING_HTML, "html.parser")
    chars = _extract_characteristics(soup)
    assert chars == {}


def test_extract_seller_joined_date(soup) -> None:
    joined = _extract_seller_joined_date(soup)
    assert joined is not None
    assert "марта 2019" in joined


def test_extract_seller_joined_date_empty() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(EMPTY_LISTING_HTML, "html.parser")
    joined = _extract_seller_joined_date(soup)
    assert joined is None


def test_extract_extra_images(soup) -> None:
    images = _extract_extra_images(soup)
    assert len(images) == 3
    assert all("avito" in img.lower() for img in images)
    # Проверяем уникальность
    assert len(set(images)) == 3


def test_extract_extra_images_empty() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(EMPTY_LISTING_HTML, "html.parser")
    images = _extract_extra_images(soup)
    assert images == []


def test_extract_seller_other_count(soup) -> None:
    count = _extract_seller_other_count(soup)
    assert count == 12


def test_extract_seller_other_count_empty() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(EMPTY_LISTING_HTML, "html.parser")
    count = _extract_seller_other_count(soup)
    assert count is None
