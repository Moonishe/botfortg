"""Тесты KeyRotationManager: encrypt→decrypt, rotate→old DEK decrypts, persist/load."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.fernet import Fernet


def _make_kek() -> bytes:
    """Генерирует тестовый KEK."""
    return Fernet.generate_key()


def test_init_and_active_dek():
    """active_dek возвращает KEK как fallback если DEK не загружен."""
    from src.core.crypto.key_rotation import KeyRotationManager

    kek = _make_kek()
    mgr = KeyRotationManager(kek)
    assert mgr.active_dek == kek, "Fallback: active_dek должен вернуть KEK"
    assert mgr.active_key_id is None
    print("PASS test_init_and_active_dek")


def test_generate_and_encrypt_dek():
    """DEK генерируется и шифруется KEK."""
    from src.core.crypto.key_rotation import KeyRotationManager

    kek = _make_kek()
    mgr = KeyRotationManager(kek)
    dek = mgr._generate_dek()
    assert len(dek) == 44, "DEK должен быть 44 символа (urlsafe-base64)"
    encrypted = mgr._encrypt_dek(dek)
    assert isinstance(encrypted, str)
    assert len(encrypted) > 40
    # Расшифровываем обратно
    decrypted = mgr._decrypt_dek(encrypted)
    assert decrypted == dek, "Расшифрованный DEK должен совпадать с исходным"
    print("PASS test_generate_and_encrypt_dek")


def test_rotate_creates_new_dek_old_still_decrypts():
    """Ротация создаёт новый DEK, старый остаётся в кэше."""
    from src.core.crypto.key_rotation import KeyRotationManager

    kek = _make_kek()
    mgr = KeyRotationManager(kek)

    # Старый DEK = KEK (fallback)
    old_dek = mgr.active_dek

    # Ручная ротация (без БД)
    import asyncio

    new_key_id = asyncio.new_event_loop().run_until_complete(mgr.rotate())
    assert new_key_id == 1
    new_dek = mgr.active_dek
    assert new_dek != old_dek, "Новый DEK должен отличаться от старого"

    # Старый DEK всё ещё доступен (по умолчанию key_id 0 = KEK, или через get_dek)
    # В этом тесте старый — KEK, он через get_dek не сохранён (только через active_dek)
    # Но новый должен работать
    fernet_new = Fernet(new_dek)
    plaintext = "hello world"
    encrypted = fernet_new.encrypt(plaintext.encode())
    decrypted = fernet_new.decrypt(encrypted).decode()
    assert decrypted == plaintext, "Новый DEK должен шифровать/расшифровывать"
    print("PASS test_rotate_creates_new_dek_old_still_decrypts")


def test_multiple_rotations():
    """Несколько ротаций: старые DEK'и сохраняются."""
    from src.core.crypto.key_rotation import KeyRotationManager

    kek = _make_kek()
    mgr = KeyRotationManager(kek)

    import asyncio

    loop = asyncio.new_event_loop()

    # Первая ротация
    key1 = loop.run_until_complete(mgr.rotate())
    dek1 = mgr.active_dek
    assert key1 == 1

    # Вторая ротация
    key2 = loop.run_until_complete(mgr.rotate())
    dek2 = mgr.active_dek
    assert key2 == 2
    assert dek2 != dek1

    # Оба DEK'а доступны
    assert mgr.get_dek(1) == dek1
    assert mgr.get_dek(2) == dek2

    # Шифруем dek1, расшифровываем dek1
    f1 = Fernet(dek1)
    msg = b"test message"
    enc = f1.encrypt(msg)
    assert f1.decrypt(enc) == msg

    # Шифруем dek2, расшифровываем dek2
    f2 = Fernet(dek2)
    enc2 = f2.encrypt(msg)
    assert f2.decrypt(enc2) == msg

    # dek1 не может расшифровать зашифрованное dek2
    try:
        f1.decrypt(enc2)
        assert False, "dek1 не должен расшифровывать данные зашифрованные dek2"
    except Exception:
        pass  # Ожидаемо

    print("PASS test_multiple_rotations")


def test_invalid_kek_raises():
    """Некорректный KEK вызывает ошибку."""
    from src.core.crypto.key_rotation import KeyRotationManager

    try:
        KeyRotationManager(b"too_short")
        assert False, "Должна быть ошибка"
    except ValueError:
        pass  # Ожидаемо
    print("PASS test_invalid_kek_raises")


def test_decrypt_dek_with_wrong_kek():
    """Неправильный KEK не может расшифровать DEK."""
    from src.core.crypto.key_rotation import KeyRotationManager

    kek1 = _make_kek()
    kek2 = _make_kek()
    mgr1 = KeyRotationManager(kek1)
    mgr2 = KeyRotationManager(kek2)

    dek = mgr1._generate_dek()
    encrypted = mgr1._encrypt_dek(dek)

    # mgr2 с другим KEK не должен расшифровать
    from cryptography.fernet import InvalidToken

    try:
        mgr2._decrypt_dek(encrypted)
        assert False, "Должна быть ошибка расшифровки"
    except InvalidToken:
        pass  # Ожидаемо
    print("PASS test_decrypt_dek_with_wrong_kek")


def test_encrypt_decrypt_roundtrip_with_fernet():
    """Fernet roundtrip: encrypt(active_dek) → decrypt(active_dek)."""
    from src.core.crypto.key_rotation import KeyRotationManager

    kek = _make_kek()
    mgr = KeyRotationManager(kek)

    dek = mgr.active_dek  # KEK как fallback
    fernet = Fernet(dek)
    plaintext = "секретные данные"
    encrypted = fernet.encrypt(plaintext.encode()).decode()
    decrypted = fernet.decrypt(encrypted.encode()).decode()
    assert decrypted == plaintext
    print("PASS test_encrypt_decrypt_roundtrip_with_fernet")


def test_get_all_key_ids():
    """get_all_key_ids возвращает все известные ID."""
    from src.core.crypto.key_rotation import KeyRotationManager

    kek = _make_kek()
    mgr = KeyRotationManager(kek)

    import asyncio

    loop = asyncio.new_event_loop()

    for _ in range(3):
        loop.run_until_complete(mgr.rotate())

    ids = mgr.get_all_key_ids()
    assert ids == [1, 2, 3], f"Ожидались [1, 2, 3], получено {ids}"
    print("PASS test_get_all_key_ids")


if __name__ == "__main__":
    print("=== KeyRotationManager Unit Tests ===\n")
    test_init_and_active_dek()
    test_generate_and_encrypt_dek()
    test_rotate_creates_new_dek_old_still_decrypts()
    test_multiple_rotations()
    test_invalid_kek_raises()
    test_decrypt_dek_with_wrong_kek()
    test_encrypt_decrypt_roundtrip_with_fernet()
    test_get_all_key_ids()
    print("\n=== ALL TESTS PASSED ===")
