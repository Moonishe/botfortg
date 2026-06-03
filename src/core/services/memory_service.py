"""Memory service layer.

Wraps the memory repository for use by handlers and commands, adding
input validation and a consistent ``user_id``-based interface.

Delegates to:
  ``src.db.repos.memory_repo`` — add_memory, search_memories, delete_memory
  ``src.db.repos.session_repo`` — user lookup when needed
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Memory, User
from src.db.repos import memory_repo
from .exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# Limits to keep DB queries bounded and predictable.
_MIN_TEXT_LENGTH: int = 3
_MAX_TEXT_LENGTH: int = 5000
_MAX_SEARCH_LIMIT: int = 100
_DEFAULT_SEARCH_LIMIT: int = 10


async def _get_user_or_raise(session: AsyncSession, user_id: int) -> User:
    """Resolve a User by primary key or raise :class:`NotFoundError`."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User with id={user_id} not found")
    return user


# ── Public API ───────────────────────────────────────────────────────────


async def save_memory(
    session: AsyncSession,
    user_id: int,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> Memory:
    """Save a memory (fact) for the user with validation.

    Validates ``text`` length and normalises it before delegating to
    :func:`memory_repo.add_memory`. The metadata dict is mapped onto
    repository keyword arguments.

    Args:
        session: Active database session.
        user_id: Internal user primary key.
        text: Fact text (3–5000 characters after stripping).
        metadata: Optional dict with keys such as:
            ``contact_id`` (int), ``sentiment`` (str), ``source`` (str),
            ``confidence`` (float 0–1), ``importance`` (float 0–1).

    Returns:
        The :class:`Memory` ORM instance returned by the repository
        (created or merged with an existing duplicate).

    Raises:
        NotFoundError: If the user does not exist.
        ValidationError: If text is too short, too long, or empty.
    """
    if not isinstance(text, str):
        raise ValidationError(
            f"Memory text must be a string, got {type(text).__name__}"
        )

    stripped = text.strip()

    if len(stripped) < _MIN_TEXT_LENGTH:
        raise ValidationError(
            f"Memory text is too short (minimum {_MIN_TEXT_LENGTH} characters, "
            f"got {len(stripped)})"
        )

    if len(stripped) > _MAX_TEXT_LENGTH:
        raise ValidationError(
            f"Memory text exceeds {_MAX_TEXT_LENGTH} characters (got {len(stripped)})"
        )

    user = await _get_user_or_raise(session, user_id)
    meta = metadata or {}

    # ── Map metadata → repo kwargs ───────────────────────────────────
    kwargs: dict[str, Any] = {"fact": stripped}

    if (contact_id := meta.get("contact_id")) is not None:
        if not isinstance(contact_id, int):
            raise ValidationError("metadata['contact_id'] must be an integer")
        kwargs["contact_id"] = contact_id

    if (sentiment := meta.get("sentiment")) is not None:
        valid_sentiments = {"positive", "negative", "neutral", "contradictory"}
        if sentiment not in valid_sentiments:
            raise ValidationError(
                f"Invalid sentiment: {sentiment!r}. Allowed: {sorted(valid_sentiments)}"
            )
        kwargs["sentiment"] = sentiment

    if (source := meta.get("source")) is not None:
        valid_sources = {"chat", "user", "weekly", "system"}
        if source not in valid_sources:
            logger.warning("Unknown memory source=%r, storing as-is", source)
        kwargs["source"] = source

    if (confidence := meta.get("confidence")) is not None:
        if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
            raise ValidationError(
                "metadata['confidence'] must be a float in range [0.0, 1.0]"
            )
        kwargs["confidence"] = float(confidence)

    if (importance := meta.get("importance")) is not None:
        if not isinstance(importance, (int, float)) or not (0.0 <= importance <= 1.0):
            raise ValidationError(
                "metadata['importance'] must be a float in range [0.0, 1.0]"
            )
        kwargs["importance"] = float(importance)

    # ── Delegation ───────────────────────────────────────────────────
    mem = await memory_repo.add_memory(session, user, **kwargs)

    # add_memory may return None if the fact is too short after stripping
    # (its own validation). We've already validated above, so this is
    # purely defensive.
    if mem is None:
        raise ValidationError("Memory could not be saved (repository returned None)")

    logger.info("Saved memory id=%d for user=%d", mem.id, user_id)
    return mem


async def search_memories(
    session: AsyncSession,
    user_id: int,
    query: str,
    limit: int = _DEFAULT_SEARCH_LIMIT,
) -> list[Memory]:
    """Search memories for a user using FTS with ILIKE fallback.

    Delegates to :func:`memory_repo.search_memories`, which tries FTS5
    full-text search first and falls back to ``ILIKE`` if no hits.

    Args:
        session: Active database session.
        user_id: Internal user primary key.
        query: Free-text search query.
        limit: Maximum number of results to return (1–100, default 10).

    Returns:
        List of :class:`Memory` instances (may be empty).

    Raises:
        NotFoundError: If the user does not exist.
        ValidationError: If query is empty or limit is out of range.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValidationError("Search query must be a non-empty string")

    if not isinstance(limit, int) or limit < 1:
        raise ValidationError(f"limit must be a positive integer (got {limit!r})")

    if limit > _MAX_SEARCH_LIMIT:
        logger.warning(
            "search_memories limit=%d exceeds max=%d, clamping",
            limit,
            _MAX_SEARCH_LIMIT,
        )
        limit = _MAX_SEARCH_LIMIT

    user = await _get_user_or_raise(session, user_id)

    results = await memory_repo.search_memories(session, user, query.strip())

    # The repo function doesn't accept limit; we clamp here.
    # search_memories returns results ordered by created_at DESC (ILIKE
    # fallback) or bm25 rank (FTS). Truncating preserves the best hits.
    return results[:limit]


async def delete_memory(
    session: AsyncSession,
    user_id: int,
    memory_id: int,
) -> bool:
    """Delete a memory, verifying ownership.

    Delegates to :func:`memory_repo.delete_memory` after ownership
    has been validated. Also invalidates the memory stats cache.

    Args:
        session: Active database session.
        user_id: Internal user primary key.
        memory_id: Primary key of the :class:`Memory` row to delete.

    Returns:
        ``True`` if the memory was deleted, ``False`` if it did not
        exist or did not belong to this user.

    Raises:
        NotFoundError: If the user does not exist.
        ValidationError: If memory_id is not a positive integer.
    """
    if not isinstance(memory_id, int) or memory_id <= 0:
        raise ValidationError(
            f"Invalid memory_id: {memory_id!r} (must be a positive integer)"
        )

    user = await _get_user_or_raise(session, user_id)

    deleted = await memory_repo.delete_memory(session, user, memory_id)

    if deleted:
        logger.info("Deleted memory id=%d for user=%d", memory_id, user_id)
    else:
        logger.debug(
            "Memory id=%d not found or not owned by user=%d", memory_id, user_id
        )

    return deleted
