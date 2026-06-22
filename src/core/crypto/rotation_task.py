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

# overlap guard: предотвращает параллельный запуск цикла ротации
_overlap_guard = asyncio.Lock()


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
            """Перешифровывает API-ключи и LLM-ключи на новый DEK.

            Пробуем все известные DEK (и KEK как fallback) для расшифровки,
            чтобы перешифровать данные, оставшиеся под предыдущими ключами
            после неполной ротации.
            """
            from cryptography.fernet import Fernet
            from sqlalchemy import select

            from src.db.models._auth import ApiKey, LlmKeySlot

            re_encrypted = 0
            errors = 0

            # Все кандидаты для расшифровки: KEK fallback + все известные DEK.
            dek_candidates = [mgr._kek_bytes, *mgr._deks.values()]

            for model_cls, label in [
                (ApiKey, "api_keys"),
                (LlmKeySlot, "llm_key_slots"),
            ]:
                result = await session.execute(select(model_cls))
                rows = result.scalars().all()
                for row in rows:
                    if not row.key_enc:
                        errors += 1
                        logger.warning("Empty key_enc for %s id=%s", label, row.id)
                        continue
                    plaintext: str | None = None
                    for dek in dek_candidates:
                        try:
                            plaintext = (
                                Fernet(dek).decrypt(row.key_enc.encode()).decode()
                            )
                            break
                        except Exception:  # noqa: S112
                            # Wrong key candidate — expected when iterating
                            # KEK fallback and all historical DEKs.
                            continue
                    if plaintext is None:
                        errors += 1
                        logger.warning(
                            "Cannot decrypt %s id=%s with any known key",
                            label,
                            row.id,
                        )
                        continue
                    try:
                        row.key_enc = (
                            Fernet(new_dek).encrypt(plaintext.encode()).decode()
                        )
                        re_encrypted += 1
                    except Exception as e:
                        errors += 1
                        logger.warning(
                            "Cannot re-encrypt %s id=%s with new DEK: %s",
                            label,
                            row.id,
                            e,
                        )

            logger.info(
                "Перешифрование завершено: %d ключей, %d ошибок",
                re_encrypted,
                errors,
            )

        # Выполняем ротацию атомарно (in-memory + БД)
        await mgr.rotate_and_persist(session, re_encrypt_callback=re_encrypt_data)


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
                key_id = await mgr.create_initial_dek(session)
                if key_id is not None:
                    await session.commit()
        except Exception:
            logger.exception(
                "Ошибка при первичной инициализации DEK "
                "(ротация продолжит работу по расписанию)"
            )

    # Бесконечный цикл с интервалом key_rotation_interval_days
    interval_sec = settings.key_rotation_interval_days * 86400

    while True:
        if _overlap_guard.locked():
            await asyncio.sleep(interval_sec)
            continue
        async with _overlap_guard:
            try:
                await _rotate_keys_async()
            except Exception:
                logger.exception(
                    "Ошибка в цикле ротации ключей — следующая попытка через %s дней",
                    settings.key_rotation_interval_days,
                )
        await asyncio.sleep(interval_sec)
