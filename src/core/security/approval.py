"""Hybrid Approval Kernel — unified HMAC-signed confirmation format.

Provides a single callback format `ap:{verb}:{action_key}:{signature}` for all
destructive or user-confirmable actions, with hybrid persistence:
- HIGH/CRITICAL/destructive actions → persistent DB route (PendingAction)
- medium/read-only actions → signed in-memory route

The user_id is included in the HMAC payload but NOT in the callback_data, so
 Telegram-side callback strings do not reveal owner identity.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Literal, cast

from src.config import settings

logger = logging.getLogger(__name__)

_ApprovalRoute = Literal["db", "memory"]
_ApprovalVerb = Literal["send", "tool", "cron", "intent"]

DB_RISKS: frozenset[str] = frozenset({"high", "critical"})


@dataclass(frozen=True)
class ApprovalDecision:
    """Decision describing how a confirmation should be persisted and routed."""

    route: _ApprovalRoute
    verb: _ApprovalVerb
    risk: str
    human_summary: str
    action_key: str
    expires_at: float | None = None
    payload_hash: str = ""
    metadata: dict = field(default_factory=dict)

    def is_db(self) -> bool:
        return self.route == "db"

    def is_memory(self) -> bool:
        return self.route == "memory"


# ponytail: cached derived HMAC key — derivation is pure, key never changes at runtime
_SECRET_CACHE: bytes | None = None
_LEGACY_SECRET_CACHE: bytes | None = None
_SECRET_LOCK = threading.Lock()
_LEGACY_SECRET_LOCK = threading.Lock()


def _secret() -> bytes:
    """Derive a domain-separated 32-byte HMAC key.

    Uses a dedicated ``approval_hmac_key`` when configured. If not set,
    generates a persistent auto-generated key stored under ``data/.approval_hmac_key``.
    """
    global _SECRET_CACHE
    if _SECRET_CACHE is not None:
        return _SECRET_CACHE

    with _SECRET_LOCK:
        if _SECRET_CACHE is not None:
            return _SECRET_CACHE

        key: str | None = settings.approval_hmac_key
        if not key:
            key_path = Path(settings.data_dir) / ".approval_hmac_key"
            if key_path.exists():
                key = key_path.read_text(encoding="utf-8").strip()
            else:
                import secrets

                key = secrets.token_hex(32)
                key_path.parent.mkdir(parents=True, exist_ok=True)
                key_path.write_text(key, encoding="utf-8")
                logger.warning(
                    "APPROVAL_HMAC_KEY not set in .env; auto-generated persistent key "
                    "saved to %s. Set APPROVAL_HMAC_KEY explicitly for multi-instance "
                    "deployments.",
                    key_path,
                )
        if not key:
            raise RuntimeError(
                "approval_hmac_key not configured and auto-generation failed — "
                "cannot compute approval HMAC"
            )
        _SECRET_CACHE = hmac.new(
            b"approval-hmac-v1", key.encode("utf-8"), hashlib.sha256
        ).digest()
        return _SECRET_CACHE


def _legacy_secret() -> bytes | None:
    """Return the old encryption_key-derived HMAC key for verification fallback.

    Only used when APPROVAL_HMAC_KEY is not configured and an existing token was
    signed with the encryption_key. New tokens are always signed with the dedicated
    approval key.
    """
    global _LEGACY_SECRET_CACHE
    if settings.approval_hmac_key:
        return None
    if _LEGACY_SECRET_CACHE is not None:
        return _LEGACY_SECRET_CACHE

    with _LEGACY_SECRET_LOCK:
        if _LEGACY_SECRET_CACHE is not None:
            return _LEGACY_SECRET_CACHE
        if settings.approval_hmac_key:
            return None
        key = settings.encryption_key
        if not key:
            return None
        _LEGACY_SECRET_CACHE = hmac.new(
            b"approval-hmac-v1", key.encode("utf-8"), hashlib.sha256
        ).digest()
        return _LEGACY_SECRET_CACHE


def _compute_hmac(
    key: bytes,
    action_key: str,
    user_id: int,
    verb: str,
    expires_at: float | None,
    payload_hash: str,
) -> str:
    """Compute HMAC with an explicit key."""
    expires_str = str(expires_at) if expires_at is not None else ""
    msg = f"{action_key}:{user_id}:{verb}:{expires_str}:{payload_hash}"
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _hash_payload(payload: dict | None) -> str:
    """Stable deterministic hash of a JSON-serializable payload."""
    if payload is None:
        return ""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def compute_hmac(
    action_key: str,
    user_id: int,
    verb: str,
    expires_at: float | None,
    payload_hash: str,
) -> str:
    """Compute a 32-hex-character (128-bit) HMAC for a confirmation action.

    Parameters
    ----------
    action_key:
        DB action id (numeric) or memory uid (alphanumeric).
    user_id:
        User identifier — Telegram user id (memory route) or DB user.id (DB route).
        Included in the HMAC but not in callback_data.
    verb:
        Action verb: send, tool, cron, intent.
    expires_at:
        Unix timestamp when the confirmation expires.
    payload_hash:
        Short deterministic hash of the action payload.
    """
    return _compute_hmac(_secret(), action_key, user_id, verb, expires_at, payload_hash)


def verify_hmac(
    signature: str,
    action_key: str,
    user_id: int,
    verb: str,
    expires_at: float | None,
    payload_hash: str,
) -> bool:
    """Verify a confirmation signature against its expected value."""
    if not signature:
        return False
    expected = compute_hmac(action_key, user_id, verb, expires_at, payload_hash)
    if hmac.compare_digest(expected, signature):
        return True
    legacy_key = _legacy_secret()
    if legacy_key is not None:
        expected_legacy = _compute_hmac(
            legacy_key, action_key, user_id, verb, expires_at, payload_hash
        )
        return hmac.compare_digest(expected_legacy, signature)
    return False


def route_for(risk: str, is_destructive: bool = False) -> _ApprovalRoute:
    """Return the persistence route for an action based on its risk.

    HIGH/CRITICAL and explicitly destructive actions are persisted to the DB so
    they survive process restarts. Everything else is kept in memory.
    """
    risk = (risk or "low").strip().lower()
    if risk in DB_RISKS or is_destructive:
        return "db"
    return "memory"


def format_callback(
    verb: _ApprovalVerb,
    action_key: str,
    signature: str,
) -> str:
    """Return the unified callback string: ``ap:{verb}:{action_key}:{signature}``."""
    return f"ap:{verb}:{action_key}:{signature}"


def format_cancel_callback(
    verb: _ApprovalVerb,
    action_key: str,
) -> str:
    """Return the unified cancel callback string: ``ap:cancel:{verb}:{action_key}``."""
    return f"ap:cancel:{verb}:{action_key}"


def parse_callback(
    data: str,
) -> tuple[_ApprovalVerb, str, str] | None:
    """Parse a unified callback string.

    Returns ``(verb, action_key, signature)`` or ``None`` if the format is wrong.
    """
    if not data or not data.startswith("ap:"):
        return None
    parts = data.split(":", 3)
    if len(parts) != 4:
        return None
    _, verb, action_key, signature = parts
    if not action_key or not signature:
        return None
    if verb not in ("send", "tool", "cron", "intent"):
        return None
    return cast(_ApprovalVerb, verb), action_key, signature


def parse_cancel_callback(
    data: str,
) -> tuple[_ApprovalVerb, str] | None:
    """Parse a unified cancel callback string.

    Returns ``(verb, action_key)`` or ``None`` if the format is wrong.
    """
    if not data or not data.startswith("ap:cancel:"):
        return None
    parts = data.split(":", 3)
    if len(parts) != 4:
        return None
    _, _, verb, action_key = parts
    if not action_key:
        return None
    if verb not in ("send", "tool", "cron", "intent"):
        return None
    return cast(_ApprovalVerb, verb), action_key


def memory_ttl() -> float:
    """Default TTL for in-memory confirmations in seconds (from settings or 5 min)."""
    return float(getattr(settings, "pending_ttl_sec", 300))


def memory_entry(
    user_id: int,
    verb: _ApprovalVerb,
    risk: str,
    human_summary: str,
    payload: dict,
    action_key: str | None = None,
    metadata: dict | None = None,
) -> tuple[str, dict]:
    """Create a signed in-memory confirmation entry.

    Returns ``(action_key, entry)`` where ``entry`` is ready to be stored in the
    in-memory confirmation dict. The entry already contains the HMAC signature.
    Optional ``metadata`` can store the concrete tool/intent name for callers.
    """
    if action_key is None:
        action_key = uuid.uuid4().hex[:12]
    expires_at = time.monotonic() + memory_ttl()
    payload_hash = _hash_payload(payload)
    signature = compute_hmac(
        action_key=action_key,
        user_id=user_id,
        verb=verb,
        expires_at=expires_at,
        payload_hash=payload_hash,
    )
    entry = {
        "action_key": action_key,
        "user_id": user_id,
        "verb": verb,
        "risk": risk,
        "human_summary": human_summary,
        "payload": payload,
        "payload_hash": payload_hash,
        "expires_at": expires_at,
        "signature": signature,
        "ts": time.monotonic(),
        "metadata": dict(metadata or {}),
    }
    return action_key, entry


def verify_memory_entry(entry: dict, user_id: int, signature: str) -> bool:
    """Verify an in-memory confirmation entry against the provided signature."""
    if not entry or not signature:
        return False
    if entry.get("user_id") != user_id:
        return False
    try:
        if time.monotonic() > float(entry.get("expires_at", 0)):
            return False
    except (TypeError, ValueError):
        return False
    return verify_hmac(
        signature=signature,
        action_key=str(entry.get("action_key", "")),
        user_id=user_id,
        verb=str(entry.get("verb", "")),
        expires_at=entry.get("expires_at"),
        payload_hash=str(entry.get("payload_hash", "")),
    )


__all__ = [
    "ApprovalDecision",
    "_hash_payload",
    "compute_hmac",
    "format_callback",
    "format_cancel_callback",
    "memory_entry",
    "memory_ttl",
    "parse_callback",
    "parse_cancel_callback",
    "route_for",
    "verify_hmac",
    "verify_memory_entry",
]
