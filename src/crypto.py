import asyncio
import logging
import threading

from cryptography.fernet import Fernet, InvalidToken

from src.config import settings

logger = logging.getLogger(__name__)


_fernet: Fernet | None = None
_fernet_lock = threading.Lock()


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.encryption_key
        if not key or len(key) != 44:
            raise ValueError("Invalid ENCRYPTION_KEY: must be 44-char urlsafe-base64")
        # NOTE: Password/key bytes remain in memory until garbage collected.
        # For sensitive deployments, use SecureString or zero the buffer after use.
        with _fernet_lock:
            if _fernet is None:
                _fernet = Fernet(key.encode())
    return _fernet


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt ciphertext trying all known keys: DEKs first, then KEK fallback.

    This integrates with the KEK/DEK rotation system. If the rotation manager
    is initialized, we try all known DEKs (active + historical) for decryption.
    If no DEK matches, we fall back to the KEK (for legacy data encrypted
    before DEK rotation was enabled).

    Without this, DEK rotation re-encrypts data with a DEK that decrypt()
    cannot handle, breaking all API keys after rotation.
    """
    # Guard: None / non-string / empty string → fail fast with clear message
    if not isinstance(ciphertext, str) or not ciphertext.strip():
        raise ValueError(
            f"ciphertext must be a non-empty string, got {type(ciphertext).__name__}"
        )

    cipher_bytes = ciphertext.encode()

    # Try DEK candidates from the rotation manager (if initialized)
    try:
        from src.core.crypto.key_rotation import get_rotation_manager

        mgr = get_rotation_manager()
        if mgr is not None:
            # All candidates: all known DEKs (active + historical).
            # KEK is NOT included — it's tried in the fallback path below.
            # ponytail: deduplicate before trying (KEK may appear in _deks
            # if it was used as a legacy DEK).
            dek_candidates = mgr.get_all_dek_bytes()
            for key_bytes in dek_candidates:
                try:
                    return Fernet(key_bytes).decrypt(cipher_bytes).decode()
                except (InvalidToken, ValueError, TypeError):
                    # ValueError: Fernet() rejects malformed keys.
                    # TypeError: non-bytes key snuck into _deks/_kek_bytes.
                    continue
            # None of the DEKs worked — fall through to KEK-only path below
    except Exception:
        logger.debug("DEK rotation manager unavailable, using KEK-only decrypt")

    # KEK-only decrypt (legacy path + fallback)
    try:
        return _get_fernet().decrypt(cipher_bytes).decode()
    except InvalidToken as exc:
        raise ValueError(
            "Не удалось расшифровать: неверный ключ или повреждённые данные"
        ) from exc


# Асинхронные обёртки — Fernet.encrypt/decrypt CPU-bound,
# в async-контексте должны вызываться через run_in_executor/to_thread.
async def encrypt_async(plaintext: str) -> str:
    """CPU-bound шифрование: использует asyncio.to_thread."""
    return await asyncio.to_thread(encrypt, plaintext)


async def decrypt_async(ciphertext: str) -> str:
    """CPU-bound дешифровка: использует asyncio.to_thread."""
    return await asyncio.to_thread(decrypt, ciphertext)
