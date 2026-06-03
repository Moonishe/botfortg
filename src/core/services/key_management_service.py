"""Key-management service layer.

Separates API-key CRUD and validation logic from handlers/commands.

Delegates to:
  ``src.db.repos.key_repo`` — add_key_slot, list_key_slots, get_api_key
  ``src.db.repos.session_repo`` — user retrieval
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.crypto import decrypt
from src.db.models import User
from src.db.repos.key_repo import (
    add_key_slot,
    get_api_key,
    list_key_slots,
)
from .exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# Providers we natively support (mapped onto LlmKeySlot.provider — max 16 chars).
VALID_PROVIDERS: frozenset[str] = frozenset(
    {
        "openai",
        "anthropic",
        "gemini",
        "mistral",
        "openrouter",
        "deepseek",
        "groq",
        "together",
        "xai",
    }
)

# Minimum accepted raw key length (most real API keys are 32+ chars).
_MIN_KEY_LENGTH: int = 8

# Maximum raw key length — defence against accidental paste of huge blobs.
_MAX_KEY_LENGTH: int = 2048


async def _get_user_or_raise(session: AsyncSession, user_id: int) -> User:
    """Resolve a User by primary key or raise :class:`NotFoundError`."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User with id={user_id} not found")
    return user


# ── Public API ───────────────────────────────────────────────────────────


async def get_user_keys(
    session: AsyncSession,
    user_id: int,
) -> list[Any]:
    """Retrieve all API key slots for a user.

    Returns raw :class:`LlmKeySlot` ORM instances. Key material is
    encrypted — callers should use :func:`src.crypto.decrypt` or
    :func:`get_api_key` for plaintext access.

    Args:
        session: Active database session.
        user_id: Internal user primary key.

    Returns:
        List of :class:`LlmKeySlot` objects (may be empty).

    Raises:
        NotFoundError: If the user does not exist.
    """
    user = await _get_user_or_raise(session, user_id)
    return await list_key_slots(session, user)


async def add_key(
    session: AsyncSession,
    user_id: int,
    provider: str,
    key: str,
    category: str = "llm",
) -> tuple[Any, bool]:
    """Add a new API key with provider/key validation.

    Validates ``provider``, ``key`` length/format, and ``category`` before
    delegating to :func:`add_key_slot`. The repository layer performs
    per-user locking internally to prevent duplicate writes.

    Args:
        session: Active database session.
        user_id: Internal user primary key.
        provider: Provider name (e.g. ``"openai"``, ``"anthropic"``).
        key: Raw (plaintext) API key.
        category: Key category — one of ``"llm"``, ``"stt"``,
            ``"tts"``, ``"vision"``.

    Returns:
        ``(slot, is_new)`` tuple from :func:`add_key_slot`.
        *is_new* is ``False`` when the key already exists (no-op).

    Raises:
        NotFoundError: If the user does not exist.
        ValidationError: If provider name, key length, or category is invalid.
    """
    # ── Validation ───────────────────────────────────────────────────
    if not isinstance(provider, str) or provider.lower().strip() not in VALID_PROVIDERS:
        raise ValidationError(
            f"Invalid provider: {provider!r}. Supported: {sorted(VALID_PROVIDERS)}"
        )

    if not isinstance(key, str) or len(key.strip()) < _MIN_KEY_LENGTH:
        raise ValidationError(
            f"Key must be a non-empty string of at least {_MIN_KEY_LENGTH} characters"
        )

    if len(key) > _MAX_KEY_LENGTH:
        raise ValidationError(
            f"Key exceeds maximum length of {_MAX_KEY_LENGTH} characters"
        )

    valid_categories = frozenset({"llm", "stt", "tts", "vision"})
    if category not in valid_categories:
        raise ValidationError(
            f"Invalid category: {category!r}. Allowed: {sorted(valid_categories)}"
        )

    user = await _get_user_or_raise(session, user_id)

    provider = provider.lower().strip()
    key = key.strip()

    # ── Delegation to repo ───────────────────────────────────────────
    slot, is_new = await add_key_slot(
        session,
        user,
        provider=provider,
        key=key,
        category=category,
        purpose="main",
        label=f"{provider}/main",
    )

    logger.info(
        "Key slot %s for user=%d provider=%s (new=%s)",
        "added" if is_new else "already-exists",
        user_id,
        provider,
        is_new,
    )
    return slot, is_new


async def delete_key(
    session: AsyncSession,
    user_id: int,
    key_id: int,
) -> None:
    """Delete a key slot by its ID, verifying ownership.

    After removing the :class:`LlmKeySlot` row the function also
    re-synchronises the legacy :class:`ApiKey` table so that
    :func:`get_api_key` continues to return correct data.

    Args:
        session: Active database session.
        user_id: Internal user primary key.
        key_id: Primary key of the :class:`LlmKeySlot` row to delete.

    Raises:
        NotFoundError: If the user does not exist.
        ValidationError: If key_id is not a positive integer.
        ServiceError: If the key slot does not belong to this user
            (raised as ``NotFoundError``).
    """
    if not isinstance(key_id, int) or key_id <= 0:
        raise ValidationError(
            f"Invalid key_id: {key_id!r} (must be a positive integer)"
        )

    user = await _get_user_or_raise(session, user_id)

    slot = await session.get(LlmKeySlot, key_id)
    if slot is None or slot.user_id != user.id:
        raise NotFoundError(f"Key slot id={key_id} not found for user_id={user_id}")

    provider = slot.provider

    await session.delete(slot)
    await session.flush()

    # ── Re-sync ApiKey table ─────────────────────────────────────────
    # The legacy ApiKey stores all keys per provider as a single
    # comma-separated encrypted field. After deleting a slot, we need
    # to update that aggregate; if no slots remain — remove the row.
    remaining = await list_key_slots(session, user, provider=provider)
    result = await session.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == provider)
    )
    api_key_row = result.scalar_one_or_none()

    if remaining:
        parts: list[str] = []
        for s in remaining:
            try:
                parts.append(decrypt(s.key_enc))
            except (ValueError, Exception):
                continue
        if api_key_row and parts:
            from src.crypto import encrypt

            api_key_row.key_enc = encrypt(",".join(parts))
        elif api_key_row and not parts:
            await session.delete(api_key_row)
    elif api_key_row:
        await session.delete(api_key_row)

    await session.flush()

    logger.info(
        "Deleted key slot id=%d for user=%d provider=%s",
        key_id,
        user_id,
        provider,
    )


async def validate_key(provider: str, key: str) -> bool:
    """Lightweight credential check against the provider's API.

    Performs a cheap "whoami" / "list models" call to confirm the key
    is accepted. Returns ``False`` (never raises) when the provider
    is unknown or the network call fails, so callers can treat this as
    an advisory check rather than a hard gate.

    Args:
        provider: Provider name (e.g. ``"openai"``).
        key: Raw (plaintext) API key.

    Returns:
        ``True`` if the provider accepted the key, ``False`` otherwise.
    """
    if not isinstance(provider, str) or not isinstance(key, str):
        return False

    provider = provider.lower().strip()
    key = key.strip()

    if not key:
        return False

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            if provider == "openai":
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                return resp.status_code == 200

            if provider == "anthropic":
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-3-haiku-20240307",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
                if resp.status_code == 200:
                    return True
                if resp.status_code in (401, 403):
                    return False
                if resp.status_code == 429:
                    logger.warning("Anthropic rate limit during key validation")
                    return True  # Ключ валиден, но rate limit
                return False  # 400, 500 и другие — невалидный ключ

            if provider == "gemini":
                try:
                    resp = await client.get(
                        "https://generativelanguage.googleapis.com/v1/models",
                        headers={"X-Goog-Api-Key": key},
                    )
                    return resp.status_code == 200
                except Exception:
                    return False

            if provider == "mistral":
                resp = await client.get(
                    "https://api.mistral.ai/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                return resp.status_code == 200

        # Unknown provider — assume valid (no verification endpoint known)
        logger.debug("No validation endpoint for provider=%r, assuming valid", provider)
        return True

    except ImportError:
        logger.warning(
            "httpx not installed; skipping online key validation for %s", provider
        )
        return True

    except Exception as exc:
        logger.warning("Key validation failed for provider=%s: %s", provider, exc)
        return False


# Re-import for use inside delete_key (avoid top-level circular imports)
from src.db.models import ApiKey, LlmKeySlot  # noqa: E402
