"""Tests for built-in GitHub, 4PDA, and X connectors."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("OWNER_TELEGRAM_ID", "123456789")

import pytest

from src.core.connectors import connector_registry, register_builtin_connectors
from src.core.connectors.base import ConnectorRuntime
from src.core.actions.tool_registry import tool_registry
from src.config import settings
from src.core.connectors.site_connectors import (
    _download_url,
    _fourpda_handler,
    _github_handler,
    _nitter_rss_url,
    _parse_tweet_ref,
    _x_handler,
    _x_rss_handler,
)


def test_builtin_site_connectors_register_standard_actions():
    register_builtin_connectors()

    for name in ("github", "4pda", "x"):
        registered = connector_registry.get(name)
        assert registered is not None
        assert {action.name for action in registered.spec.actions} == {
            "search_topics",
            "read_topic",
            "read_post",
            "download_attachments",
        }


@pytest.mark.asyncio
async def test_github_search_maps_repositories_and_issues(monkeypatch):
    async def fake_fetch_json(url, *, params=None, headers=None, allowed_hosts=None):
        if "repositories" in url:
            return {
                "items": [
                    {
                        "id": 1,
                        "full_name": "owner/repo",
                        "html_url": "https://github.com/owner/repo",
                        "description": "repo desc",
                        "stargazers_count": 42,
                    }
                ]
            }
        return {
            "items": [
                {
                    "id": 2,
                    "title": "Issue title",
                    "html_url": "https://github.com/owner/repo/issues/1",
                    "state": "open",
                    "repository_url": "https://api.github.com/repos/owner/repo",
                }
            ]
        }

    monkeypatch.setattr(
        "src.core.connectors.site_connectors._fetch_json", fake_fetch_json
    )

    result = await _github_handler(
        "search_topics", {"query": "mcp", "limit": 5}, ConnectorRuntime()
    )

    assert result.ok is True
    assert result.data["repositories"][0]["full_name"] == "owner/repo"
    assert result.data["issues_and_prs"][0]["title"] == "Issue title"


@pytest.mark.asyncio
async def test_fourpda_read_topic_extracts_posts_and_attachments(monkeypatch):
    html = """
    <html><head><title>Topic title</title></head><body>
      <div id="post-123">
        <a href="index.php?act=attach&type=post&id=456">firmware.zip</a>
        <p>Hello\u202eworld</p>
      </div>
    </body></html>
    """

    async def fake_fetch_text(url, *, params=None, headers=None, allowed_hosts=None):
        return html

    monkeypatch.setattr(
        "src.core.connectors.site_connectors._fetch_text", fake_fetch_text
    )

    result = await _fourpda_handler(
        "read_topic",
        {"url": "https://4pda.to/forum/index.php?showtopic=1"},
        ConnectorRuntime(),
    )

    assert result.ok is True
    assert result.data["title"] == "Topic title"
    assert result.data["posts"][0]["id"] == "post-123"
    assert "Helloworld" in result.data["posts"][0]["text"]
    assert result.data["posts"][0]["attachments"][0]["text"] == "firmware.zip"


@pytest.mark.asyncio
async def test_fourpda_download_returns_links_without_explicit_opt_in(monkeypatch):
    # Patch settings directly — pydantic-settings reads env vars ONCE at init,
    # so monkeypatch.delenv() does NOT affect an already-initialized settings singleton.
    monkeypatch.setattr(settings, "fourpda_allow_restricted_downloads", False)
    html = """
    <html><body>
      <a href="/forum/dl/post/456/firmware.zip">firmware.zip</a>
    </body></html>
    """

    async def fake_fetch_text(url, *, params=None, headers=None, allowed_hosts=None):
        return html

    monkeypatch.setattr(
        "src.core.connectors.site_connectors._fetch_text", fake_fetch_text
    )

    result = await _fourpda_handler(
        "download_attachments",
        {"url": "https://4pda.to/forum/index.php?showtopic=1"},
        ConnectorRuntime(),
    )

    assert result.ok is True
    assert result.data["count"] == 0
    assert result.data["attachments"][0]["text"] == "firmware.zip"
    assert "disabled by default" in result.data["note"]


@pytest.mark.asyncio
async def test_x_connector_requires_bearer_token(monkeypatch):
    # Patch settings directly — pydantic-settings reads env vars ONCE at init.
    monkeypatch.setattr(settings, "x_bearer_token", "")
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)

    result = await _x_handler("search_topics", {"query": "grok"}, ConnectorRuntime())

    assert result.ok is False
    assert "X_BEARER_TOKEN" in result.error


@pytest.mark.asyncio
async def test_x_read_post_uses_api_when_token_exists(monkeypatch):
    # Patch settings directly — pydantic-settings reads env vars ONCE at init.
    monkeypatch.setattr(settings, "x_bearer_token", "token")
    monkeypatch.setenv("X_BEARER_TOKEN", "token")

    async def fake_fetch_json(url, *, params=None, headers=None, allowed_hosts=None):
        return {"data": {"id": "123", "text": "hello"}}

    monkeypatch.setattr(
        "src.core.connectors.site_connectors._fetch_json", fake_fetch_json
    )

    result = await _x_handler(
        "read_post", {"url": "https://x.com/i/status/123"}, ConnectorRuntime()
    )

    assert result.ok is True
    assert result.data["data"]["id"] == "123"


@pytest.mark.asyncio
async def test_builtin_download_actions_require_confirmation():
    register_builtin_connectors()

    blocked = await tool_registry.execute(
        "mcp_connectors",
        action="execute",
        connector="github",
        connector_action="download_attachments",
        params={"repo": "owner/repo"},
        _confirmed=False,
    )

    assert blocked["ok"] is False
    assert blocked["error"] == "requires confirmation"
    assert blocked["metadata"]["risk"] == "high"


@pytest.mark.asyncio
async def test_fourpda_rejects_missing_and_external_urls():
    missing = await _fourpda_handler("read_topic", {}, ConnectorRuntime())
    external = await _fourpda_handler(
        "read_topic", {"url": "https://example.com/forum"}, ConnectorRuntime()
    )
    insecure = await _fourpda_handler(
        "read_topic",
        {"url": "http://4pda.to/forum/index.php?showtopic=1"},
        ConnectorRuntime(),
    )

    assert missing.ok is False
    assert missing.error == "url or id is required"
    assert external.ok is False
    assert "4PDA URL must use" in external.error
    assert insecure.ok is False
    assert "4PDA URL must use" in insecure.error


@pytest.mark.asyncio
async def test_download_url_blocks_private_network_before_request(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("src.core.connectors.site_connectors.DOWNLOAD_ROOT", tmp_path)

    with pytest.raises(Exception, match="non-public address"):
        await _download_url("http://127.0.0.1/file.zip", "test", "file.zip")


def test_redirect_headers_strip_sensitive_values_cross_host():
    from src.core.connectors.site_connectors import _redirect_headers

    headers = {
        "Authorization": "Bearer secret",
        "Cookie": "session=secret",
        "User-Agent": "test",
    }

    same_host = _redirect_headers(
        headers, "https://github.com/a", "https://github.com/b"
    )
    cross_host = _redirect_headers(
        headers, "https://github.com/a", "https://objects.githubusercontent.com/b"
    )

    assert same_host == headers
    assert cross_host == {"User-Agent": "test"}


@pytest.mark.asyncio
async def test_fetch_response_blocks_private_network_redirect(monkeypatch):
    from src.core.connectors.site_connectors import _fetch_response

    def fake_is_public(host):
        return host == "example.com"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None, headers=None):
            return SimpleNamespace(
                is_redirect=True,
                headers={"location": "http://127.0.0.1/private"},
                url=url,
            )

    # _resolve_is_public_ip is the blocking DNS resolver that
    # validate_public_url runs via asyncio.to_thread. Patching it (rather than
    # the async wrapper) lets us control resolution synchronously.
    monkeypatch.setattr(
        "src.core.connectors.http._resolve_is_public_ip", fake_is_public
    )
    monkeypatch.setattr(
        "src.core.connectors.site_connectors.httpx.AsyncClient", FakeClient
    )

    with pytest.raises(Exception, match="non-public address"):
        await _fetch_response("https://example.com/file.zip")


# ---------------------------------------------------------------------------
# x_rss (Nitter RSS) connector tests
# ---------------------------------------------------------------------------

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
<title>testuser / X</title>
<atom:link href="https://nitter.net/testuser/rss" rel="self"/>
<item>
<title>Test tweet one</title>
<link>https://nitter.net/testuser/status/111</link>
<guid isPermaLink="false">https://x.com/testuser/status/111</guid>
<description>First tweet content</description>
<pubDate>Mon, 01 Jan 2026 12:00:00 GMT</pubDate>
</item>
<item>
<title>Another tweet</title>
<link>https://nitter.net/testuser/status/222</link>
<guid isPermaLink="false">https://x.com/testuser/status/222</guid>
<description>Second tweet with image</description>
<pubDate>Mon, 01 Jan 2026 13:00:00 GMT</pubDate>
<enclosure url="https://pbs.twimg.com/media/img.jpg" type="image/jpeg"/>
</item>
</channel>
</rss>"""

SEARCH_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Nitter search</title>
<item>
<title>Search result tweet</title>
<link>https://nitter.net/otheruser/status/333</link>
<guid>https://x.com/otheruser/status/333</guid>
<description>Search match</description>
</item>
</channel>
</rss>"""


def test_x_rss_spec_registered():
    """x_rss connector must be registered after register_builtin_connectors()."""
    register_builtin_connectors()
    registered = connector_registry.get("x_rss")
    assert registered is not None
    assert registered.spec.name == "x_rss"
    assert registered.spec.category == "social"
    assert registered.spec.auth_mode == "none"
    assert set(registered.spec.capabilities) == {
        "search_topics",
        "read_topic",
        "read_post",
        "download_attachments",
    }


def test_nitter_rss_url_extracts_username():
    """_nitter_rss_url must extract username from various input formats."""
    assert _nitter_rss_url("@jack") == "https://nitter.net/jack/rss"
    assert _nitter_rss_url("jack") == "https://nitter.net/jack/rss"
    assert _nitter_rss_url("https://x.com/jack") == "https://nitter.net/jack/rss"
    assert _nitter_rss_url("https://twitter.com/jack") == "https://nitter.net/jack/rss"
    assert (
        _nitter_rss_url("https://x.com/jack/status/123")
        == "https://nitter.net/jack/rss"
    )
    assert _nitter_rss_url("invalid!@#") is None
    assert _nitter_rss_url("") is None


def test_parse_tweet_ref_extracts_username_and_id():
    """_parse_tweet_ref must extract (username, tweet_id) from tweet URLs."""
    assert _parse_tweet_ref("https://x.com/user/status/123456") == ("user", "123456")
    assert _parse_tweet_ref("https://twitter.com/user/status/123456") == (
        "user",
        "123456",
    )
    assert _parse_tweet_ref("https://nitter.net/user/status/123456") == (
        "user",
        "123456",
    )
    assert _parse_tweet_ref("@user/status/123") == ("user", "123")
    assert _parse_tweet_ref("user/status/456") == ("user", "456")
    assert _parse_tweet_ref("789012") == ("", "789012")
    assert _parse_tweet_ref("https://x.com/user") == ("user", "")
    assert _parse_tweet_ref("just_a_username") == ("", "")


@pytest.mark.asyncio
async def test_x_rss_search_topics(monkeypatch):
    """x_rss search_topics must fetch RSS and return parsed entries."""

    async def fake_fetch_text(url, *, params=None, headers=None, allowed_hosts=None):
        return SEARCH_RSS_XML

    monkeypatch.setattr(
        "src.core.connectors.site_connectors._fetch_text", fake_fetch_text
    )

    result = await _x_rss_handler(
        "search_topics", {"query": "python", "limit": 5}, ConnectorRuntime()
    )

    assert result.ok is True
    assert result.data["query"] == "python"
    assert len(result.data["results"]) == 1
    assert result.data["results"][0]["title"] == "Search result tweet"
    assert "otheruser" in result.data["results"][0]["link"]


@pytest.mark.asyncio
async def test_x_rss_search_requires_query(monkeypatch):
    """x_rss search_topics must fail when query is missing."""
    result = await _x_rss_handler("search_topics", {"query": ""}, ConnectorRuntime())
    assert result.ok is False
    assert "query is required" in result.error


@pytest.mark.asyncio
async def test_x_rss_read_topic(monkeypatch):
    """x_rss read_topic must fetch user timeline RSS and return entries."""

    async def fake_fetch_text(url, *, params=None, headers=None, allowed_hosts=None):
        return RSS_XML

    monkeypatch.setattr(
        "src.core.connectors.site_connectors._fetch_text", fake_fetch_text
    )

    result = await _x_rss_handler(
        "read_topic", {"url": "@testuser", "limit": 20}, ConnectorRuntime()
    )

    assert result.ok is True
    assert result.data["username"] == "testuser"
    assert len(result.data["results"]) == 2
    assert result.data["results"][0]["title"] == "Test tweet one"


@pytest.mark.asyncio
async def test_x_rss_read_topic_requires_valid_input(monkeypatch):
    """x_rss read_topic must fail for invalid/empty input."""
    result = await _x_rss_handler("read_topic", {"url": ""}, ConnectorRuntime())
    assert result.ok is False
    assert "X/Twitter profile URL or @username" in result.error


@pytest.mark.asyncio
async def test_x_rss_read_post(monkeypatch):
    """x_rss read_post must find a specific tweet by ID in user timeline RSS."""

    async def fake_fetch_text(url, *, params=None, headers=None, allowed_hosts=None):
        return RSS_XML

    monkeypatch.setattr(
        "src.core.connectors.site_connectors._fetch_text", fake_fetch_text
    )

    result = await _x_rss_handler(
        "read_post",
        {"url": "https://x.com/testuser/status/222"},
        ConnectorRuntime(),
    )

    assert result.ok is True
    assert result.data["title"] == "Another tweet"


@pytest.mark.asyncio
async def test_x_rss_read_post_tweet_not_found(monkeypatch):
    """x_rss read_post must fail when tweet ID not in timeline RSS."""

    async def fake_fetch_text(url, *, params=None, headers=None, allowed_hosts=None):
        return RSS_XML

    monkeypatch.setattr(
        "src.core.connectors.site_connectors._fetch_text", fake_fetch_text
    )

    result = await _x_rss_handler(
        "read_post",
        {"url": "https://x.com/testuser/status/999"},
        ConnectorRuntime(),
    )

    assert result.ok is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_x_rss_read_post_requires_full_url(monkeypatch):
    """x_rss read_post must fail for bare tweet ID without username."""
    result = await _x_rss_handler("read_post", {"id": "123456"}, ConnectorRuntime())
    assert result.ok is False
    assert "tweet URL" in result.error


@pytest.mark.asyncio
async def test_x_rss_unsupported_action():
    """x_rss handler must return error for unsupported actions."""
    result = await _x_rss_handler("unknown_action", {}, ConnectorRuntime())
    assert result.ok is False
    assert "Unsupported x_rss action" in result.error
