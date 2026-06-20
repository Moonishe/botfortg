"""Тесты для трёх новых MCP-инструментов:
- mcp_exchange (курсы валют и конвертация)
- mcp_photo_search (поиск изображений через DuckDuckGo)
- _genius_handler (поиск песен через Genius API)
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# mock-ответ API валют (open.er-api.com)
# ═══════════════════════════════════════════════════════════════════════════

_SAMPLE_RATES = {
    "result": "success",
    "base_code": "USD",
    "rates": {"EUR": 0.92, "RUB": 88.5, "GBP": 0.79, "JPY": 149.3, "CNY": 7.24},
    "time_last_update_utc": "Sun, 13 Jun 2026 12:00:00 +0000",
}


def _make_fake_httpx_response(json_payload: dict) -> SimpleNamespace:
    """Создать fake-ответ httpx с json-методом и raise_for_status."""
    resp = SimpleNamespace()
    resp.json = lambda: json_payload  # type: ignore[method-assign]
    resp.raise_for_status = lambda: None  # type: ignore[method-assign]
    return resp


# ═══════════════════════════════════════════════════════════════════════════
# Тесты mcp_exchange
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate_exchange(monkeypatch):
    """Изолировать модуль mcp_exchange от сети во всех тестах ниже."""

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, *args, **kwargs):
            # url имеет вид https://open.er-api.com/v6/latest/USD
            if "/latest/USD" in url:
                payload = _SAMPLE_RATES
            elif "/latest/EUR" in url:
                payload = {
                    "result": "success",
                    "base_code": "EUR",
                    "rates": {"USD": 1.087, "RUB": 96.2},
                    "time_last_update_utc": "Sun, 13 Jun 2026 12:00:00 +0000",
                }
            else:
                payload = _SAMPLE_RATES
            return _make_fake_httpx_response(payload)

    monkeypatch.setattr(
        "src.core.actions.mcp_exchange.httpx.AsyncClient", FakeAsyncClient
    )


class TestMcpExchange:
    """Тесты инструмента mcp_exchange."""

    @pytest.mark.asyncio
    async def test_get_rates_возвращает_курсы_с_полями_base_rates_updated(self):
        """get_rates должен вернуть dict с ok, base, rates, updated."""
        from src.core.actions.mcp_exchange import mcp_exchange

        result = await mcp_exchange(action="get_rates", base="USD")

        assert result["ok"] is True
        assert result["base"] == "USD"
        assert isinstance(result["rates"], dict)
        assert "EUR" in result["rates"]
        assert result["rates"]["EUR"] == 0.92
        assert "updated" in result
        assert "rates_count" in result
        assert "total_currencies" in result

    @pytest.mark.asyncio
    async def test_convert_100_usd_to_eur_возвращает_корректную_сумму(self):
        """Конвертация 100 USD → EUR должна вернуть правильную сумму."""
        from src.core.actions.mcp_exchange import mcp_exchange

        result = await mcp_exchange(
            action="convert", amount=100, from_currency="USD", to_currency="EUR"
        )

        assert result["ok"] is True
        assert result["amount"] == 100
        assert result["from"] == "USD"
        assert result["to"] == "EUR"
        assert result["rate"] == 0.92
        assert result["result"] == round(100 * 0.92, 4)

    @pytest.mark.asyncio
    async def test_invalid_action_возвращает_ошибку(self):
        """Неизвестный action должен вернуть error."""
        from src.core.actions.mcp_exchange import mcp_exchange

        result = await mcp_exchange(action="destroy_currency")

        assert "error" in result
        assert "Неизвестное действие" in result["error"]


# ═══════════════════════════════════════════════════════════════════════════
# Тесты mcp_photo_search
# ═══════════════════════════════════════════════════════════════════════════

_SAMPLE_IMAGES = [
    {
        "title": "Котёнок спит",
        "image": "https://example.com/kitty1.jpg",
        "thumbnail": "https://example.com/kitty1_thumb.jpg",
        "url": "https://example.com/kitty1_page",
        "width": 800,
        "height": 600,
        "source": "Example",
    },
    {
        "title": "Кошка на окне",
        "image": "https://example.com/kitty2.jpg",
        "thumbnail": "https://example.com/kitty2_thumb.jpg",
        "url": "https://example.com/kitty2_page",
        "width": 1024,
        "height": 768,
        "source": "Example",
    },
]


class TestMcpPhotoSearch:
    """Тесты инструмента mcp_photo_search."""

    def _install_ddgs_mock(self, monkeypatch, images=None):
        """Подставить fake DDGS с заданным списком картинок."""
        if images is None:
            images = _SAMPLE_IMAGES

        class FakeDDGS:
            def __init__(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def images(self, query, max_results=10):
                return images[:max_results]

        monkeypatch.setattr("duckduckgo_search.DDGS", FakeDDGS)

    @pytest.mark.asyncio
    async def test_search_images_возвращает_список_результатов(self, monkeypatch):
        """search_images должен вернуть ok, query, results, count."""
        self._install_ddgs_mock(monkeypatch)

        from src.core.actions.mcp_photo_search import mcp_photo_search

        result = await mcp_photo_search(
            action="search_images", query="котики", max_results=10
        )

        assert result["ok"] is True
        assert result["query"] == "котики"
        assert isinstance(result["results"], list)
        assert len(result["results"]) == 2
        assert result["results"][0]["title"] == "Котёнок спит"
        assert result["results"][0]["image_url"] == "https://example.com/kitty1.jpg"
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_search_images_пустой_query_возвращает_ошибку(self, monkeypatch):
        """Пустой query должен вернуть error."""
        self._install_ddgs_mock(monkeypatch)

        from src.core.actions.mcp_photo_search import mcp_photo_search

        result = await mcp_photo_search(
            action="search_images", query="", max_results=10
        )

        assert "error" in result
        assert "query обязателен" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_action_возвращает_ошибку(self, monkeypatch):
        """Неизвестный action должен вернуть error."""
        self._install_ddgs_mock(monkeypatch)

        from src.core.actions.mcp_photo_search import mcp_photo_search

        result = await mcp_photo_search(action="destroy_photos")

        assert "error" in result
        assert "Неизвестное действие" in result["error"]


# ═══════════════════════════════════════════════════════════════════════════
# Тесты _genius_handler (Genius Lyrics connector)
# ═══════════════════════════════════════════════════════════════════════════

_SAMPLE_GENIUS_SEARCH = {
    "response": {
        "hits": [
            {
                "result": {
                    "id": 101,
                    "title": "Bohemian Rhapsody",
                    "primary_artist": {"name": "Queen"},
                    "url": "https://genius.com/Queen-bohemian-rhapsody-lyrics",
                    "song_art_image_thumbnail_url": "https://example.com/thumb.jpg",
                    "header_image_thumbnail_url": "",
                }
            },
            {
                "result": {
                    "id": 202,
                    "title": "Another One Bites the Dust",
                    "primary_artist": {"name": "Queen"},
                    "url": "https://genius.com/Queen-another-one-bites-the-dust-lyrics",
                    "song_art_image_thumbnail_url": "",
                    "header_image_thumbnail_url": "https://example.com/thumb2.jpg",
                }
            },
        ]
    }
}


@pytest.fixture(autouse=True)
def _isolate_genius(monkeypatch):
    """Гарантировать fake-токен Genius и изолировать сеть."""
    monkeypatch.setenv("GENIUS_ACCESS_TOKEN", "fake-genius-token")


class TestGeniusHandler:
    """Тесты Genius-коннектора (_genius_handler)."""

    def _install_genius_json_mock(self, monkeypatch, payload=None):
        """Подставить fake _fetch_json для Genius API."""
        if payload is None:
            payload = _SAMPLE_GENIUS_SEARCH

        async def fake_fetch_json(
            url, *, params=None, headers=None, allowed_hosts=None
        ):
            return payload

        monkeypatch.setattr(
            "src.core.connectors.site_connectors._fetch_json", fake_fetch_json
        )

    @pytest.mark.asyncio
    async def test_search_songs_возвращает_список_песен(self, monkeypatch):
        """search_songs action должен вернуть songs с title, artist, url."""
        self._install_genius_json_mock(monkeypatch)

        from src.core.connectors.site_connectors import _genius_handler
        from src.core.connectors.base import ConnectorRuntime

        result = await _genius_handler(
            "search_songs", {"query": "queen", "limit": 5}, ConnectorRuntime()
        )

        assert result.ok is True
        assert "songs" in result.data
        songs = result.data["songs"]
        assert len(songs) == 2
        assert songs[0]["title"] == "Bohemian Rhapsody"
        assert songs[0]["artist"] == "Queen"
        assert songs[0]["url"] == "https://genius.com/Queen-bohemian-rhapsody-lyrics"
        assert songs[0]["id"] == 101
        assert songs[0]["thumbnail"] == "https://example.com/thumb.jpg"
        assert result.data["query"] == "queen"

    @pytest.mark.asyncio
    async def test_search_by_lyrics_возвращает_lyrics_matches(self, monkeypatch):
        """search_by_lyrics action должен вернуть lyrics_matches."""
        self._install_genius_json_mock(monkeypatch)

        from src.core.connectors.site_connectors import _genius_handler
        from src.core.connectors.base import ConnectorRuntime

        result = await _genius_handler(
            "search_by_lyrics", {"query": "mama just killed a man"}, ConnectorRuntime()
        )

        assert result.ok is True
        assert "lyrics_matches" in result.data
        assert len(result.data["lyrics_matches"]) == 2

    @pytest.mark.asyncio
    async def test_missing_token_возвращает_ошибку(self, monkeypatch):
        """Отсутствие GENIUS_ACCESS_TOKEN должно вернуть ошибку."""
        monkeypatch.delenv("GENIUS_ACCESS_TOKEN", raising=False)

        from src.core.connectors.site_connectors import _genius_handler
        from src.core.connectors.base import ConnectorRuntime

        result = await _genius_handler(
            "search_songs", {"query": "queen"}, ConnectorRuntime()
        )

        assert result.ok is False
        assert result.error is not None
        assert "GENIUS_ACCESS_TOKEN" in result.error

    @pytest.mark.asyncio
    async def test_search_songs_пустой_query_возвращает_ошибку(self, monkeypatch):
        """Пустой query в search_songs должен вернуть ошибку."""
        from src.core.connectors.site_connectors import _genius_handler
        from src.core.connectors.base import ConnectorRuntime

        result = await _genius_handler(
            "search_songs", {"query": ""}, ConnectorRuntime()
        )

        assert result.ok is False
        assert result.error is not None
        assert "query обязателен" in result.error

    @pytest.mark.asyncio
    async def test_invalid_action_возвращает_ошибку(self, monkeypatch):
        """Неизвестный action должен вернуть error."""
        self._install_genius_json_mock(monkeypatch)

        from src.core.connectors.site_connectors import _genius_handler
        from src.core.connectors.base import ConnectorRuntime

        result = await _genius_handler("destroy_music", {}, ConnectorRuntime())

        assert result.ok is False
        assert result.error is not None
        assert "Неподдерживаемое действие genius" in result.error
