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


class PairingManager:
    """Security layer: unknown contacts must be approved before interaction."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._pending: dict[int, str] = {}  # sender_id → code
        self._allowlist: set[int] = set()
        # asyncio.Lock: methods are async and must not block the event loop.
        self._lock = asyncio.Lock()
        self._pending_path = (
            data_dir or Path(settings.data_dir)
        ) / "pending_pairings.json"
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

    def _save_pending(self) -> None:
        """Persist pending pairings to disk."""
        try:
            self._pending_path.parent.mkdir(parents=True, exist_ok=True)
            self._pending_path.write_text(
                json.dumps(self._pending, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("Failed to save pending pairings", exc_info=True)

    async def is_allowed(self, sender_id: int) -> bool:
        """Check in-memory first, then DB fallback."""
        async with self._lock:
            if sender_id in self._allowlist:
                return True
        # DB check
        try:
            async with get_session() as session:
                from src.db.repo import is_contact_allowed

                allowed = await is_contact_allowed(session, sender_id)
                if allowed:
                    # Cache in memory for speed
                    async with self._lock:
                        self._allowlist.add(sender_id)
                return allowed
        except Exception:
            return False

    async def is_pending(self, sender_id: int) -> bool:
        async with self._lock:
            return sender_id in self._pending

    async def start_pairing(self, sender_id: int) -> str:
        """Generate a pairing code for a new contact."""
        code = secrets.token_hex(16)  # 32-char hex, 128 bits — bruteforce-resistant
        async with self._lock:
            self._pending[sender_id] = code
            await asyncio.to_thread(self._save_pending)
        logger.info("Pairing started for sender %d", sender_id)
        return code

    async def approve(self, sender_id: int, code: str) -> bool:
        """Approve a pending contact and persist to DB."""
        async with self._lock:
            if sender_id in self._pending and self._pending[sender_id] == code:
                self._allowlist.add(sender_id)
                logger.info("Pairing approved: sender %d", sender_id)
                approved = True
            else:
                approved = False
        if approved:
            # Persist to DB BEFORE removing from _pending.
            # If DB write fails the pairing code is preserved for retry.
            try:
                async with get_session() as session:
                    from src.db.repo import add_allowed_contact

                    await add_allowed_contact(session, sender_id)
                # Only clean up pending after successful DB persist.
                async with self._lock:
                    self._pending.pop(sender_id, None)
                    await asyncio.to_thread(self._save_pending)
            except Exception:
                logger.exception("Failed to persist pairing")
                # Откат in-memory состояния при ошибке БД
                async with self._lock:
                    self._allowlist.discard(sender_id)
                return False
            return True
        return False

    async def revoke(self, sender_id: int) -> None:
        """Remove from allowlist (in-memory + DB)."""
        async with self._lock:
            self._allowlist.discard(sender_id)
            self._pending.pop(sender_id, None)
            await asyncio.to_thread(self._save_pending)
        try:
            async with get_session() as session:
                from src.db.repo import remove_allowed_contact

                await remove_allowed_contact(session, sender_id)
        except Exception:
            logger.exception("Failed to remove allowed contact from DB")

    async def allowlist_size(self) -> int:
        async with self._lock:
            return len(self._allowlist)

    async def pending_count(self) -> int:
        async with self._lock:
            return len(self._pending)


# Module-level singleton
pairing = PairingManager()
