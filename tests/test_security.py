"""Security module tests: crypto, SSRF guard, pairing, key guard, OpenAI provider."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Only async tests get the marker; sync tests are left plain.
# Avoid module-level marker to suppress warnings on sync methods.

# ── Helpers ──────────────────────────────────────────────────────────────

_VALID_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # 44-char valid Fernet key
_OTHER_KEY = "BAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # different valid key


def _make_mock_completion(content: str) -> MagicMock:
    """Build a mock OpenAI chat completion with the given content."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    completion = MagicMock()
    completion.choices = [choice]
    return completion


# ══════════════════════════════════════════════════════════════════════════
# 1.  Crypto — encrypt / decrypt roundtrip, invalid key, garbage
# ══════════════════════════════════════════════════════════════════════════


class TestCrypto:
    """src.crypto — Fernet-based symmetric encryption."""

    def _reset_fernet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Force _get_fernet() to re-read settings.encryption_key."""
        import src.crypto as _crypto

        monkeypatch.setattr(_crypto, "_fernet", None)

    # ── roundtrip ────────────────────────────────────────────────────────

    def test_encrypt_decrypt_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Encrypt then decrypt with the same key returns the original text."""
        self._reset_fernet(monkeypatch)
        monkeypatch.setattr("src.config.settings.encryption_key", _VALID_KEY)

        from src.crypto import decrypt, encrypt

        plain = "Hello, secret world!"
        encrypted = encrypt(plain)
        assert isinstance(encrypted, str)
        assert encrypted != plain, "ciphertext should differ from plaintext"

        decrypted = decrypt(encrypted)
        assert decrypted == plain

    # ── invalid key length ───────────────────────────────────────────────

    def test_invalid_key_length_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-44-char key should raise ValueError on first encrypt/decrypt."""
        self._reset_fernet(monkeypatch)
        monkeypatch.setattr("src.config.settings.encryption_key", "too-short")

        from src.crypto import encrypt

        with pytest.raises(ValueError, match="Invalid ENCRYPTION_KEY"):
            encrypt("whatever")

    # ── decrypt garbage (wrong key) ──────────────────────────────────────

    def test_decrypt_with_wrong_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decrypting with a different key should raise a user-facing ValueError."""
        self._reset_fernet(monkeypatch)
        monkeypatch.setattr("src.config.settings.encryption_key", _VALID_KEY)

        from src.crypto import decrypt, encrypt

        cipher = encrypt("my secret")
        assert isinstance(cipher, str) and len(cipher) > 0

        # Switch to a different key and reset the cache
        self._reset_fernet(monkeypatch)
        monkeypatch.setattr("src.config.settings.encryption_key", _OTHER_KEY)

        with pytest.raises(ValueError) as exc_info:
            decrypt(cipher)
        msg = str(exc_info.value).lower()
        assert "неверный ключ" in msg or "расшифровать" in msg


# ══════════════════════════════════════════════════════════════════════════
# 2.  SSRF guard — validate_base_url
# ══════════════════════════════════════════════════════════════════════════


class TestSSRFGuard:
    """src.core.security.ssrf_guard — SSRF prevention via validate_base_url()."""

    pytestmark = pytest.mark.asyncio

    # ── blocked: loopback IP ─────────────────────────────────────────────

    async def test_blocks_loopback_ip(self) -> None:
        """127.0.0.1 should be rejected as loopback."""
        from src.core.security.ssrf_guard import validate_base_url

        with pytest.raises(ValueError, match="(?i)blocked|loopback|not allowed"):
            validate_base_url("http://127.0.0.1:8080/v1")

    # ── blocked: private network ─────────────────────────────────────────

    async def test_blocks_private_ip(self) -> None:
        """192.168.x.x should be rejected as private network."""
        from src.core.security.ssrf_guard import validate_base_url

        with pytest.raises(ValueError, match="private"):
            validate_base_url("http://192.168.1.100/api")

    # ── blocked: localhost hostname ──────────────────────────────────────

    async def test_blocks_localhost_hostname(self) -> None:
        """'localhost' hostname should be rejected before DNS resolution."""
        from src.core.security.ssrf_guard import validate_base_url

        with pytest.raises(ValueError, match="(?i)blocked|localhost|not allowed"):
            validate_base_url("https://localhost:3000")

    # ── blocked: IPv6-mapped loopback ────────────────────────────────────

    async def test_blocks_ipv6_mapped_loopback(self) -> None:
        """::ffff:127.0.0.1 (IPv4-mapped IPv6 loopback) should be rejected."""
        from src.core.security.ssrf_guard import validate_base_url

        with pytest.raises(ValueError, match="loopback"):
            validate_base_url("http://[::ffff:127.0.0.1]:8080")

    # ── allowed: public URL ──────────────────────────────────────────────

    async def test_allows_public_url(self) -> None:
        """A well-known public API URL should pass validation."""
        from src.core.security.ssrf_guard import validate_base_url

        url = "https://api.openai.com/v1"
        result = validate_base_url(url)
        assert result == url


# ══════════════════════════════════════════════════════════════════════════
# 3.  Pairing — PairingManager security layer
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestPairing:
    """src.core.security.pairing — PairingManager approval flow."""

    @pytest.fixture(autouse=True)
    def _no_db(self) -> None:
        """Isolate tests from the database — mock get_session and repo functions."""
        mock_session = MagicMock()

        @asynccontextmanager
        async def _fake_session():
            yield mock_session

        with (
            patch("src.core.security.pairing.get_session", _fake_session),
            patch(
                "src.db.repo.is_contact_allowed",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("src.db.repo.add_allowed_contact", new_callable=AsyncMock),
            patch("src.db.repo.remove_allowed_contact", new_callable=AsyncMock),
        ):
            yield

    # ── unknown sender ───────────────────────────────────────────────────

    async def test_unknown_sender_not_allowed(self) -> None:
        """A sender not in the allowlist should be rejected."""
        from src.core.security.pairing import PairingManager

        pm = PairingManager()
        allowed = await pm.is_allowed(sender_id=999)
        assert allowed is False

    # ── is_pending ───────────────────────────────────────────────────────

    async def test_is_pending_after_start(self) -> None:
        """After start_pairing, is_pending should return True."""
        from src.core.security.pairing import PairingManager

        pm = PairingManager()
        assert pm.is_pending(42) is False
        pm.start_pairing(42)
        assert pm.is_pending(42) is True

    # ── approve flow ─────────────────────────────────────────────────────

    async def test_pairing_approve_flow(self) -> None:
        """start_pairing → approve with correct code → is_allowed returns True."""
        from src.core.security.pairing import PairingManager

        pm = PairingManager()
        code = pm.start_pairing(sender_id=1)
        assert len(code) == 32  # token_hex(16) → 32 hex chars

        ok = await pm.approve(sender_id=1, code=code)
        assert ok is True
        assert await pm.is_allowed(sender_id=1) is True
        assert pm.is_pending(1) is False  # no longer pending after approval

    # ── wrong code rejected ──────────────────────────────────────────────

    async def test_wrong_code_rejected(self) -> None:
        """Approving with an incorrect code should fail."""
        from src.core.security.pairing import PairingManager

        pm = PairingManager()
        pm.start_pairing(sender_id=2)
        ok = await pm.approve(sender_id=2, code="wrong00")
        assert ok is False
        assert await pm.is_allowed(sender_id=2) is False  # not added to allowlist

    # ── revoke removes from allowed ──────────────────────────────────────

    async def test_revoke_removes_from_allowed(self) -> None:
        """After revoke, a previously approved sender should no longer be allowed."""
        from src.core.security.pairing import PairingManager

        pm = PairingManager()
        code = pm.start_pairing(sender_id=3)
        await pm.approve(sender_id=3, code=code)
        assert await pm.is_allowed(sender_id=3) is True

        await pm.revoke(sender_id=3)
        assert await pm.is_allowed(sender_id=3) is False
        assert pm.allowlist_size == 0


# ══════════════════════════════════════════════════════════════════════════
# 4.  Key guard — mask_keys, safe_str
# ══════════════════════════════════════════════════════════════════════════


class TestKeyGuard:
    """src.core.infra.key_guard — API-key masking utilities."""

    # ── OpenAI key ───────────────────────────────────────────────────────

    def test_masks_openai_key(self) -> None:
        """An OpenAI-style sk-... key should be replaced with ***."""
        from src.core.infra.key_guard import mask_keys

        text = 'api_key="sk-abc123xyz456def789ghijklmnopqrs"'
        result = mask_keys(text)
        assert "***" in result
        assert re.search(r"sk-[A-Za-z0-9]{20,}", result) is None  # no raw key

    # ── Telegram token ───────────────────────────────────────────────────

    def test_masks_telegram_token(self) -> None:
        """A Telegram bot token (digits:AA...) should be replaced with ***."""
        from src.core.infra.key_guard import mask_keys

        text = "token=1234567890:AAExampleTokenStringForTestingPurposes"
        result = mask_keys(text)
        assert "***" in result
        assert re.search(r"\d{8,10}:[\w-]{35,}", result) is None

    # ── None input ───────────────────────────────────────────────────────

    def test_none_input(self) -> None:
        """mask_keys(None) should return None unchanged."""
        from src.core.infra.key_guard import mask_keys

        assert mask_keys(None) is None  # type: ignore[arg-type]

    # ── non-string input ─────────────────────────────────────────────────

    def test_non_string_input(self) -> None:
        """mask_keys(int) should return the int unchanged."""
        from src.core.infra.key_guard import mask_keys

        assert mask_keys(42) == 42  # type: ignore[arg-type]

    # ── safe_str wraps exception ─────────────────────────────────────────

    def test_safe_str_masks_in_exception(self) -> None:
        """safe_str should mask keys inside an exception's message."""
        from src.core.infra.key_guard import safe_str

        exc = ValueError("sk-abc123xyz456def789ghijklmnopqrs leaked!")
        result = safe_str(exc)
        assert "***" in result
        assert "sk-abc" not in result


# ══════════════════════════════════════════════════════════════════════════
# 5.  OpenAI provider — chat, error handling, base_url validation, models
# ══════════════════════════════════════════════════════════════════════════


class TestOpenAIProvider:
    """src.llm.openai_provider — OpenAIProvider chat & init."""

    # ── chat resolves response ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_chat_resolves_response(self) -> None:
        """chat() should return the assistant content from the API response."""
        from src.llm.base import ChatMessage
        from src.llm.openai_provider import OpenAIProvider

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_mock_completion("Hello from AI!")
        )

        with patch("src.llm.openai_provider.AsyncOpenAI", return_value=mock_client):
            provider = OpenAIProvider(api_key="sk-test-fake")
            result = await provider.chat([ChatMessage(role="user", content="Hi")])
        assert result == "Hello from AI!"

    # ── chat error handling ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_chat_error_propagates(self) -> None:
        """chat() should propagate API errors (not swallow them)."""
        from src.llm.base import ChatMessage
        from src.llm.openai_provider import OpenAIProvider

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("API call failed")
        )

        with patch("src.llm.openai_provider.AsyncOpenAI", return_value=mock_client):
            provider = OpenAIProvider(api_key="sk-test-fake")
            with pytest.raises(Exception, match="API call failed"):
                await provider.chat([ChatMessage(role="user", content="Hi")])

    # ── _validate_base_url called on init ────────────────────────────────

    def test_validate_base_url_called_on_init(self) -> None:
        """SSRF validation runs in __init__ — valid public base_url passes."""
        from src.llm.openai_provider import OpenAIProvider

        with patch("src.llm.openai_provider.AsyncOpenAI"):
            provider = OpenAIProvider(
                api_key="sk-test-fake", base_url="https://api.openai.com/v1"
            )
        assert provider is not None

    # ── model resolution defaults ────────────────────────────────────────

    def test_model_resolution_default_light(self) -> None:
        """No model given → _resolve_model(heavy=False) returns gpt-4o-mini."""
        from src.llm.openai_provider import OpenAIProvider, OPENAI_CHAT_LIGHT

        with patch("src.llm.openai_provider.AsyncOpenAI"):
            provider = OpenAIProvider(api_key="sk-test-fake")
        assert provider._resolve_model(heavy=False) == OPENAI_CHAT_LIGHT

    def test_model_resolution_default_heavy(self) -> None:
        """No model given → _resolve_model(heavy=True) returns gpt-4o."""
        from src.llm.openai_provider import OpenAIProvider, OPENAI_CHAT_HEAVY

        with patch("src.llm.openai_provider.AsyncOpenAI"):
            provider = OpenAIProvider(api_key="sk-test-fake")
        assert provider._resolve_model(heavy=True) == OPENAI_CHAT_HEAVY

    def test_model_resolution_custom(self) -> None:
        """Custom model → _resolve_model returns the custom model always."""
        from src.llm.openai_provider import OpenAIProvider

        with patch("src.llm.openai_provider.AsyncOpenAI"):
            provider = OpenAIProvider(api_key="sk-test-fake", model="gpt-4-turbo")
        assert provider._resolve_model(heavy=False) == "gpt-4-turbo"
        assert provider._resolve_model(heavy=True) == "gpt-4-turbo"
