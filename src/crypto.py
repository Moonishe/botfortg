from cryptography.fernet import Fernet, InvalidToken

from src.config import settings


_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.encryption_key
        if not key or len(key) != 44:
            raise ValueError("Invalid ENCRYPTION_KEY: must be 44-char urlsafe-base64")
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
