"""Интеграционные тесты: обогащение объявлений (listing enrichment)."""

import pytest

from src.core.avito.service import (
    SearchParams,
    ScanResult,
    _enrich_listings,
    _compare_with_db,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: _compare_with_db
# ═══════════════════════════════════════════════════════════════════════════


def test_compare_with_db_no_existing() -> None:
    """Без existing — всё должно считаться новым."""
    parsed = [
        {"avito_id": "123", "title": "iPhone", "price": 50000},
        {"avito_id": "456", "title": "MacBook", "price": 80000},
    ]
    new_listings, price_changes, unchanged = _compare_with_db(parsed, None)
    assert len(new_listings) == 2
    assert len(price_changes) == 0
    assert len(unchanged) == 0


def test_compare_with_db_new_listing() -> None:
    """Новое объявление должно попасть в new_listings."""
    parsed = [{"avito_id": "123", "title": "iPhone", "price": 50000}]
    existing = {"456": {"price": 80000}}
    new_listings, price_changes, unchanged = _compare_with_db(parsed, existing)
    assert len(new_listings) == 1
    assert new_listings[0]["avito_id"] == "123"
    assert len(price_changes) == 0
    assert len(unchanged) == 0


def test_compare_with_db_price_change() -> None:
    """Изменение цены должно попасть в price_changes."""
    parsed = [{"avito_id": "123", "title": "iPhone", "price": 45000}]
    existing = {"123": {"price": 50000}}
    new_listings, price_changes, unchanged = _compare_with_db(parsed, existing)
    assert len(new_listings) == 0
    assert len(price_changes) == 1
    assert price_changes[0]["previous_price"] == 50000
    assert len(unchanged) == 0


def test_compare_with_db_unchanged() -> None:
    """Без изменений — объявление в unchanged."""
    parsed = [{"avito_id": "123", "title": "iPhone", "price": 50000}]
    existing = {"123": {"price": 50000}}
    new_listings, price_changes, unchanged = _compare_with_db(parsed, existing)
    assert len(new_listings) == 0
    assert len(price_changes) == 0
    assert len(unchanged) == 1


def test_compare_with_db_no_avito_id() -> None:
    """Объявление без avito_id считается новым."""
    parsed = [{"title": "iPhone", "price": 50000}]
    existing = {}
    new_listings, price_changes, unchanged = _compare_with_db(parsed, existing)
    assert len(new_listings) == 1
    assert len(price_changes) == 0
    assert len(unchanged) == 0


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: SearchParams
# ═══════════════════════════════════════════════════════════════════════════


def test_search_params_defaults() -> None:
    params = SearchParams(
        city="moskva",
        category="tovary_dlya_kompyutera",
        query="macbook",
    )
    assert params.city == "moskva"
    assert params.query == "macbook"
    assert params.price_min is None
    assert params.price_max is None


def test_search_params_with_limits() -> None:
    params = SearchParams(
        city="spb",
        category="telefony",
        query="iphone",
        price_min=10000,
        price_max=50000,
    )
    assert params.price_min == 10000
    assert params.price_max == 50000


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: ScanResult
# ═══════════════════════════════════════════════════════════════════════════


def test_scan_result_empty() -> None:
    result = ScanResult()
    assert result.listings == []
    assert result.new_listings == []
    assert result.error is None
    assert result.total_parsed == 0


def test_scan_result_to_dict() -> None:
    result = ScanResult()
    result.url = "https://avito.ru/test"
    result.total_parsed = 5
    result.new_listings = [{"id": "1"}]
    d = result.to_dict()
    assert d["url"] == "https://avito.ru/test"
    assert d["total_parsed"] == 5
    assert len(d["new_listings"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: _enrich_listings (мок)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_enrich_listings_empty() -> None:
    """Пустой список — без ошибок."""
    await _enrich_listings([], limit=10)
    # Не должно быть исключений


@pytest.mark.asyncio
async def test_enrich_listings_no_urls() -> None:
    """Объявления без URL пропускаются."""
    parsed = [
        {"avito_id": "1", "title": "Test", "deal_score": {"score": 80}},
    ]
    # Не должно упасть даже без мока
    await _enrich_listings(parsed, limit=10)
    # Проверяем что объявление не изменено
    assert "full_description" not in parsed[0]


@pytest.mark.asyncio
async def test_enrich_listings_zero_limit() -> None:
    """limit=0 — пропускаем."""
    parsed = [
        {
            "avito_id": "1",
            "title": "Test",
            "url": "https://avito.ru/test",
            "deal_score": {"score": 80},
        },
    ]
    await _enrich_listings(parsed, limit=0)
    assert "full_description" not in parsed[0]
