import asyncio
import threading

from cryptography.fernet import Fernet, InvalidToken

from src.config import settings


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
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
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
