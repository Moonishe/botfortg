"""Фоновая задача: периодическая ротация DEK (KEK/DEK key rotation).

Архитектура:
    KEK никогда не покидает память процесса.
    DEK'и хранятся в БД зашифрованными KEK.
    Ротация создаёт новый DEK, сохраняет старый для расшифровки.

Запускается при старте, если key_rotation_enabled=True.
"""

from __future__ import annotations

import asyncio
import logging

from src.config import settings
from src.core.infra.task_manager import task_manager

logger = logging.getLogger(__name__)

# Флаг для предотвращения множественного запуска
_initialized: bool = False


async def _rotate_keys_async() -> None:
    """Асинхронная ротация ключей с перешифрованием данных в БД."""
    from src.core.crypto.key_rotation import get_rotation_manager
    from src.db.session import get_session

    mgr = get_rotation_manager()
    if mgr is None:
        logger.error("KeyRotationManager не инициализирован — пропускаю ротацию")
        return

    async with get_session() as session:
        # Загружаем существующие ключи из БД
        await mgr.load_from_db(session)

        # Определяем callback для перешифрования данных
        async def re_encrypt_data(old_dek: bytes, new_dek: bytes) -> None:
            """Перешифровывает API-ключи и LLM-ключи со старого DEK на новый."""
            from sqlalchemy import select

            from src.db.models._auth import ApiKey, LlmKeySlot

            re_encrypted = 0
            errors = 0

            for model_cls, label in [
                (ApiKey, "api_keys"),
                (LlmKeySlot, "llm_key_slots"),
            ]:
                result = await session.execute(select(model_cls))
                rows = result.scalars().all()
                for row in rows:
                    try:
                        # Пробуем расшифровать старым DEK
                        from cryptography.fernet import Fernet

                        old_fernet = Fernet(old_dek)
                        plaintext = old_fernet.decrypt(row.key_enc.encode()).decode()
                        # Перешифровываем новым DEK
                        new_fernet = Fernet(new_dek)
                        row.key_enc = new_fernet.encrypt(plaintext.encode()).decode()
                        re_encrypted += 1
                    except Exception as e:
                        errors += 1
                        logger.warning(
                            "Не удалось перешифровать %s id=%s: %s",
                            label,
                            row.id,
                            e,
                        )

            logger.info(
                "Перешифрование завершено: %d ключей, %d ошибок",
                re_encrypted,
                errors,
            )

        # Выполняем ротацию
        old_key_id = mgr.active_key_id
        new_key_id = await mgr.rotate(re_encrypt_callback=re_encrypt_data)

        # Сохраняем результат в БД
        new_dek_bytes = mgr.get_dek(new_key_id)
        if new_dek_bytes is None:
            logger.error("DEK key_id=%s не найден после ротации", new_key_id)
            return
        encrypted_new_dek = mgr._encrypt_dek(new_dek_bytes)
        await mgr.persist_rotation(
            session,
            new_key_id,
            encrypted_new_dek,
            old_key_id=old_key_id,
        )
        logger.info(
            "Ротация DEK сохранена в БД: %s → %s",
            old_key_id,
            new_key_id,
        )


@task_manager.task(
    "key-rotation",
    restart_on_failure=True,
    restart_delay=60.0,
)
async def key_rotation_loop() -> None:
    """Бесконечный цикл: периодическая ротация DEK."""
    global _initialized

    if not settings.key_rotation_enabled:
        logger.info("Key rotation отключена в настройках — задача завершена")
        return

    # Первая ротация: при старте проверяем, есть ли ключи в БД
    # Если нет — создаём первый DEK
    if not _initialized:
        _initialized = True
        try:
            from src.db.session import get_session

            from src.core.crypto.key_rotation import get_rotation_manager

            mgr = get_rotation_manager()
            if mgr is None:
                logger.error(
                    "KeyRotationManager не инициализирован — key_rotation_loop завершён"
                )
                return

            async with get_session() as session:
                await mgr.load_from_db(session)
                if mgr.active_key_id is None:
                    # Первый запуск: создаём начальный DEK.
                    # Запрашиваем MAX(key_id) из БД вместо хардкода 1,
                    # чтобы избежать перезаписи старого DEK, оставшегося
                    # от предыдущего развёртывания.
                    logger.info("Первый запуск ротации — создаю начальный DEK")
                    new_dek = mgr._generate_dek()
                    from sqlalchemy import select, func
                    from src.db.models._encryption import EncryptionKey

                    max_result = await session.execute(
                        select(func.max(EncryptionKey.key_id))
                    )
                    max_id = max_result.scalar()
                    key_id = (max_id or 0) + 1
                    mgr._deks[key_id] = new_dek
                    mgr._active_key_id = key_id
                    encrypted_dek = mgr._encrypt_dek(new_dek)
                    await mgr.save_to_db(session, key_id, encrypted_dek, is_active=True)
                    await session.commit()
                    logger.info("Начальный DEK создан (key_id=%s)", key_id)
        except Exception:
            logger.exception(
                "Ошибка при первичной инициализации DEK "
                "(ротация продолжит работу по расписанию)"
            )

    # Бесконечный цикл с интервалом key_rotation_interval_days
    interval_sec = settings.key_rotation_interval_days * 86400

    while True:
        await asyncio.sleep(interval_sec)
        try:
            await _rotate_keys_async()
        except Exception:
            logger.exception(
                "Ошибка в цикле ротации ключей — следующая попытка через %s дней",
                settings.key_rotation_interval_days,
            )
