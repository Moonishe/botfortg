#!/usr/bin/env python3
"""Ручной запуск ротации ключей (KEK/DEK).

Использование:
    python scripts/rotate_keys.py

Требования:
    - .env с ENCRYPTION_KEY (KEK)
    - Таблица encryption_keys в БД (создаётся автоматически, если отсутствует)
    - cryptography установлен (pip install cryptography)

Процесс:
    1. Загружает существующие DEK'и из БД (или создаёт первый)
    2. Создаёт новый DEK
    3. Перешифровывает все API-ключи и LLM-ключи новым DEK
    4. Сохраняет результат в БД
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def _ensure_table() -> None:
    """Создаёт таблицу encryption_keys, если её ещё нет."""
    from src.config import PROJECT_ROOT
    from src.db.session import engine

    async with engine.begin() as conn:
        from sqlalchemy import text

        # Проверяем существование таблицы
        result = await conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='encryption_keys'"
            )
        )
        if result.first() is None:
            print("Таблица encryption_keys не найдена — создаю...")
            from src.db.models._encryption import EncryptionKey

            await conn.run_sync(EncryptionKey.metadata.create_all)
            print("Таблица encryption_keys создана.")
        else:
            print("Таблица encryption_keys уже существует.")


async def _load_env() -> None:
    """Загружает переменные из .env."""
    from pathlib import Path

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        import os

        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def _rotate_keys() -> None:
    """Основная логика ротации."""
    from cryptography.fernet import Fernet

    from src.config import settings
    from src.core.crypto.key_rotation import KeyRotationManager
    from src.db.session import get_session

    # Получаем KEK из настроек
    kek_str = settings.encryption_key
    if not kek_str or len(kek_str) != 44:
        print(
            "ОШИБКА: ENCRYPTION_KEY должен быть ровно 44 символа "
            "(32-байтовый ключ в urlsafe-base64).\n"
            "Сгенерируйте: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
        sys.exit(1)

    kek = kek_str.encode()
    mgr = KeyRotationManager(kek)

    async with get_session() as session:
        # Загружаем существующие ключи из БД
        await mgr.load_from_db(session)

        if mgr.active_key_id is None:
            print("DEK не найден в БД — создаю первый ключ...")
            new_dek = mgr._generate_dek()
            key_id = 1
            mgr._deks[key_id] = new_dek
            mgr._active_key_id = key_id
            encrypted_dek = mgr._encrypt_dek(new_dek)
            await mgr.save_to_db(session, key_id, encrypted_dek, is_active=True)
            await session.commit()
            print(f"Создан начальный DEK (key_id={key_id})")
            print(
                "Ротация не требуется — это первый ключ. "
                "Данные уже зашифрованы KEK, перешифрование не нужно."
            )
            return

        old_key_id = mgr.active_key_id
        old_dek = mgr.active_dek

        print(f"Текущий активный DEK: key_id={old_key_id}")
        print("Запуск ротации...")

        # Перешифрование данных
        from sqlalchemy import select
        from src.db.models._auth import ApiKey, LlmKeySlot

        re_encrypted = 0
        errors = 0

        # Ротация: создаём новый DEK
        new_key_id = await mgr.rotate()
        new_dek = mgr.active_dek
        print(
            f"Новый DEK создан: key_id={new_key_id}, "
            f"старый key_id={old_key_id} сохранён для расшифровки"
        )

        # Перешифровываем данные
        for model_cls, label in [
            (ApiKey, "api_keys"),
            (LlmKeySlot, "llm_key_slots"),
        ]:
            result = await session.execute(select(model_cls))
            rows = result.scalars().all()
            print(f"  {label}: найдено {len(rows)} записей")
            for row in rows:
                try:
                    old_fernet = Fernet(old_dek)
                    plaintext = old_fernet.decrypt(row.key_enc.encode()).decode()
                    new_fernet = Fernet(new_dek)
                    row.key_enc = new_fernet.encrypt(plaintext.encode()).decode()
                    re_encrypted += 1
                except Exception as e:
                    errors += 1
                    print(f"  ПРЕДУПРЕЖДЕНИЕ: {label} id={row.id}: {e}")

        await session.commit()

        # Сохраняем результат ротации в БД
        encrypted_new_dek = mgr._encrypt_dek(new_dek)
        await mgr.persist_rotation(
            session, new_key_id, encrypted_new_dek, old_key_id=old_key_id
        )

        print(f"\nГотово: перешифровано {re_encrypted} ключей, ошибок: {errors}")
        if errors:
            print(
                "ВНИМАНИЕ: некоторые ключи не удалось перешифровать. "
                "Проверьте логи выше."
            )
        else:
            print("УСПЕХ: все ключи перешифрованы.")
        print(
            f"Активный DEK теперь key_id={new_key_id}. "
            f"Старый key_id={old_key_id} сохранён для расшифровки старых данных."
        )


def main() -> None:
    """Точка входа."""
    print("=== Key Rotation Utility ===")
    asyncio.run(_load_env())
    asyncio.run(_ensure_table())
    asyncio.run(_rotate_keys())
    print("\nГотово.")


if __name__ == "__main__":
    main()
