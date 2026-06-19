"""Pairing manager — approve contacts before auto-reply."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from pathlib import Path

from src.config import settings
from src.db.session import get_session

logger = logging.getLogger(__name__)

# ponytail: persistent file is best-effort recovery storage, not a transaction log.
# Atomic write via temp-file + rename avoids losing all pending codes on crash.
_PENDING_FILE = "pending_pairings.json"


class PairingManager:
    """Security layer: unknown contacts must be approved before interaction."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._pending: dict[int, str] = {}  # sender_id → code
        self._allowlist: set[int] = set()
        # asyncio.Lock: methods are async and must not block the event loop.
        self._lock: asyncio.Lock = asyncio.Lock()
        self._pending_path: Path = (data_dir or settings.data_dir) / _PENDING_FILE
        self._load_pending()

    def _load_pending(self) -> None:
        """Load pending pairings from disk so codes survive process restarts."""
        if not self._pending_path.exists():
            return
        try:
            data = json.loads(self._pending_path.read_text(encoding="utf-8"))
            for k, v in data.items():
                try:
                    self._pending[int(k)] = str(v)
                except (ValueError, TypeError):
                    continue
        except Exception:
            logger.warning("Failed to load pending pairings", exc_info=True)

    def _save_pending(self, pending: dict[int, str]) -> None:
        """Persist a snapshot of pending pairings to disk atomically."""
        try:
            self._pending_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._pending_path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(pending, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self._pending_path)
        except Exception:
            logger.warning("Failed to save pending pairings", exc_info=True)

    async def _persist_pending(self) -> None:
        """Take a snapshot under the lock and persist it in a worker thread."""
        async with self._lock:
            snapshot = dict(self._pending)
        await asyncio.to_thread(self._save_pending, snapshot)

    @staticmethod
    def _validate_sender_id(sender_id: int) -> None:
        if not isinstance(sender_id, int):
            raise TypeError(f"sender_id must be int, got {type(sender_id).__name__}")

    async def is_allowed(self, sender_id: int) -> bool:
        """Check in-memory first, then DB fallback.

        If the DB says the contact is not allowed, evict any stale in-memory
        entry left behind by a race with revoke().
        """
        self._validate_sender_id(sender_id)
        async with self._lock:
            if sender_id in self._allowlist:
                return True
        try:
            async with get_session() as session:
                from src.db.repo import is_contact_allowed

                allowed = await is_contact_allowed(session, sender_id)
                async with self._lock:
                    if allowed:
                        self._allowlist.add(sender_id)
                    else:
                        # Remove stale cache from a race with revoke().
                        self._allowlist.discard(sender_id)
                return allowed
        except Exception:
            logger.debug(
                "is_allowed DB check failed for sender %d", sender_id, exc_info=True
            )
            return False

    async def is_pending(self, sender_id: int) -> bool:
        """Return True if a pairing code has been generated but not approved.

        Deprecated in production: start_pairing is now idempotent and callers
        should use it directly. Kept for tests and backward compatibility.
        """
        self._validate_sender_id(sender_id)
        async with self._lock:
            return sender_id in self._pending

    async def start_pairing(self, sender_id: int) -> str:
        """Generate or reuse a pairing code for a contact.

        If the sender already has a pending code, return it instead of
        generating a new one. This removes the race between ``is_pending``
        and ``start_pairing`` in the caller.
        """
        self._validate_sender_id(sender_id)
        async with self._lock:
            if sender_id in self._pending:
                return self._pending[sender_id]
            code = secrets.token_hex(16)  # 32-char hex, 128 bits — bruteforce-resistant
            self._pending[sender_id] = code
        await self._persist_pending()
        logger.info("Pairing started for sender %d", sender_id)
        return code

    async def approve(self, sender_id: int, code: str) -> bool:
        """Approve a pending contact and persist to DB."""
        self._validate_sender_id(sender_id)
        async with self._lock:
            if not (sender_id in self._pending and self._pending[sender_id] == code):
                return False

        # Persist to DB BEFORE updating in-memory state.
        # If DB write fails, in-memory pending code is preserved for retry.
        try:
            async with get_session() as session:
                from src.db.repo import add_allowed_contact

                await add_allowed_contact(session, sender_id)
        except Exception:
            logger.exception("Failed to persist pairing")
            return False

        async with self._lock:
            self._allowlist.add(sender_id)
            self._pending.pop(sender_id, None)
        await self._persist_pending()
        logger.info("Pairing approved: sender %d", sender_id)
        return True

    async def revoke(self, sender_id: int) -> None:
        """Remove from allowlist (in-memory + DB)."""
        self._validate_sender_id(sender_id)
        # Remove from DB first; only clear in-memory state on success.
        # This prevents a fail-open where a restart re-warms a revoked contact.
        try:
            async with get_session() as session:
                from src.db.repo import remove_allowed_contact

                await remove_allowed_contact(session, sender_id)
        except Exception:
            logger.exception("Failed to remove allowed contact from DB")
            return

        async with self._lock:
            self._allowlist.discard(sender_id)
            self._pending.pop(sender_id, None)
        await self._persist_pending()

    async def allowlist_size(self) -> int:
        async with self._lock:
            return len(self._allowlist)

    async def warm_allowlist(self) -> None:
        """Preload allowlist from DB into memory to avoid cold-start latency.

        Called once at bot startup. On error, logs warning and returns
        without crashing — is_allowed() will lazily populate on access.
        """
        try:
            async with get_session() as session:
                from src.db.repo import list_allowed_contacts

                ids = await list_allowed_contacts(session)
                async with self._lock:
                    self._allowlist.update(ids)
                logger.info("Warmed allowlist: %d contacts loaded", len(ids))
        except Exception:
            logger.warning("Failed to warm allowlist from DB", exc_info=True)

    async def pending_count(self) -> int:
        async with self._lock:
            return len(self._pending)


# Module-level singleton
pairing = PairingManager()
