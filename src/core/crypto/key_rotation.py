"""KEK/DEK key rotation manager — Fernet encryption key lifecycle.

Архитектура:
    KEK (key-encrypting-key) = encryption_key из .env (НИКОГДА не в БД)
    DEK (data-encrypting-key) = per-rotation ключ (зашифрован KEK, хранится в БД)

Процесс ротации:
    1. Генерируем новый DEK → используем для НОВЫХ шифрований
    2. Старый DEK остаётся для РАСШИФРОВКИ существующих данных
    3. Перешифровываем данные новым DEK при scheduled rotation (фоновая задача)
"""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class KeyRotationManager:
    """Управление жизненным циклом ключей шифрования.

    KEK оборачивает DEK'и через Fernet.encrypt().
    Активный DEK возвращается через active_dek.
    Старые DEK'и сохраняются для расшифровки legacy-данных.
    """

    def __init__(self, kek: bytes, db_path: str | None = None):
        """Инициализация менеджера ротации.

        Args:
            kek: KEK из settings.encryption_key (44-char urlsafe-base64).
            db_path: путь к SQLite БД (опционально, для прямого доступа).
        """
        if not kek or len(kek) != 44:
            raise ValueError(
                "KEK должен быть ровно 44 символа (32-байтовый ключ в urlsafe-base64). "
                "Сгенерируйте: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        self._kek_bytes: bytes = kek
        self._kek_fernet: Fernet = Fernet(kek)
        self._db_path: str | None = db_path

        # In-memory кэш: key_id → DEK (bytes, уже расшифрованный)
        self._deks: dict[int, bytes] = {}
        # ID активного DEK
        self._active_key_id: int | None = None

    @property
    def active_dek(self) -> bytes:
        """Текущий активный DEK для шифрования новых данных.

        Если DEK ещё не загружен — генерирует новый Fernet-ключ
        и использует его как DEK (fallback для первого запуска без БД).

        Returns:
            32-байтовый ключ (urlsafe-base64 encoded).
        """
        if self._active_key_id is not None and self._active_key_id in self._deks:
            return self._deks[self._active_key_id]

        # Fallback: если БД ещё не инициализирована — возвращаем KEK как DEK
        # (обратная совместимость с существующими данными)
        logger.warning(
            "active_dek: DEK не найден в кэше — возвращаю KEK как fallback "
            "(данные, зашифрованные KEK, будут расшифрованы корректно)"
        )
        return self._kek_bytes

    def get_dek(self, key_id: int) -> bytes | None:
        """Получить DEK по ID для расшифровки старых данных.

        Args:
            key_id: идентификатор ключа из таблицы encryption_keys.

        Returns:
            DEK в виде bytes (urlsafe-base64) или None, если ключ не найден.
        """
        return self._deks.get(key_id)

    @property
    def active_key_id(self) -> int | None:
        """ID активного ключа (для записи в зашифрованные данные)."""
        return self._active_key_id

    def _generate_dek(self) -> bytes:
        """Генерирует новый DEK — случайный Fernet-совместимый ключ."""
        return Fernet.generate_key()

    def _encrypt_dek(self, dek: bytes) -> str:
        """Шифрует DEK с помощью KEK для хранения в БД.

        Args:
            dek: сырой DEK (32 bytes, urlsafe-base64).

        Returns:
            Fernet-токен (строка), готовый для хранения в encrypted_dek.
        """
        # Используем KEK Fernet для шифрования DEK.
        # DEK уже является Fernet-ключом (44 chars urlsafe-base64),
        # шифруем его как обычную строку.
        return self._kek_fernet.encrypt(dek).decode()

    def _decrypt_dek(self, encrypted_dek: str) -> bytes:
        """Расшифровывает DEK из БД с помощью KEK.

        Args:
            encrypted_dek: Fernet-токен из поля encrypted_dek.

        Returns:
            DEK в виде bytes (urlsafe-base64).

        Raises:
            InvalidToken: если KEK не подходит или данные повреждены.
        """
        return self._kek_fernet.decrypt(encrypted_dek.encode())

    async def rotate(
        self,
        re_encrypt_callback: Callable[[bytes, bytes], Any] | None = None,
    ) -> int:
        """Ротация активного DEK: создаёт новый, сохраняет старый.

        Старый DEK остаётся в in-memory кэше для расшифровки.
        Новый DEK становится активным для шифрования.

        Args:
            re_encrypt_callback: async callable(old_dek: bytes, new_dek: bytes) -> None.
                Вызывается для перешифрования данных со старого DEK на новый.
                Если не передан — ротация проходит без перешифрования.

        Returns:
            ID нового активного ключа.
        """
        old_dek = self.active_dek
        old_key_id = self._active_key_id

        new_dek = self._generate_dek()
        new_key_id = (old_key_id or 0) + 1  # простой инкремент

        # Сохраняем новый DEK в in-memory кэш
        self._deks[new_key_id] = new_dek
        self._active_key_id = new_key_id

        logger.info(
            "Ротация DEK: старый key_id=%s, новый key_id=%s",
            old_key_id,
            new_key_id,
        )

        # Перешифрование данных (если callback передан)
        if re_encrypt_callback is not None:
            try:
                await re_encrypt_callback(old_dek, new_dek)
                logger.info(
                    "Перешифрование данных завершено: %s → %s", old_key_id, new_key_id
                )
            except Exception:
                logger.exception(
                    "Ошибка при перешифровании данных (старый DEK сохранён, "
                    "новый активен, часть данных может быть под старым ключом)"
                )

        return new_key_id

    async def load_from_db(self, session) -> None:
        """Загружает существующие DEK'и из БД в in-memory кэш.

        Args:
            session: SQLAlchemy AsyncSession.
        """
        from sqlalchemy import select

        from src.db.models._encryption import EncryptionKey

        try:
            result = await session.execute(
                select(EncryptionKey).order_by(EncryptionKey.key_id)
            )
            rows = result.scalars().all()
        except Exception as e:
            logger.warning(
                "Не удалось загрузить ключи из БД (таблица encryption_keys "
                "возможно не существует): %s",
                e,
            )
            return

        for row in rows:
            try:
                dek = self._decrypt_dek(row.encrypted_dek)
                self._deks[row.key_id] = dek
                if row.is_active:
                    self._active_key_id = row.key_id
                logger.debug("Загружен DEK key_id=%s из БД", row.key_id)
            except InvalidToken:
                logger.error(
                    "Не удалось расшифровать DEK key_id=%s — KEK не совпадает "
                    "или данные повреждены. Ключ пропущен.",
                    row.key_id,
                )
            except Exception:
                logger.exception(
                    "Неожиданная ошибка при загрузке DEK key_id=%s",
                    row.key_id,
                )

        if self._deks and self._active_key_id is None:
            # Если нет активного ключа — используем последний загруженный
            self._active_key_id = max(self._deks.keys())
            logger.warning(
                "Активный DEK не найден в БД — использую последний key_id=%s",
                self._active_key_id,
            )

        logger.info(
            "Загружено DEK'ов из БД: %d, активный: %s",
            len(self._deks),
            self._active_key_id,
        )

    async def save_to_db(
        self,
        session,
        key_id: int,
        encrypted_dek: str,
        is_active: bool = False,
    ) -> None:
        """Сохраняет DEK (зашифрованный KEK) в БД.

        Args:
            session: SQLAlchemy AsyncSession.
            key_id: идентификатор ключа.
            encrypted_dek: Fernet-токен (DEK, зашифрованный KEK).
            is_active: является ли этот ключ активным.
        """
        from src.db.models._encryption import EncryptionKey

        # Проверяем, существует ли уже запись
        from sqlalchemy import select

        existing = await session.execute(
            select(EncryptionKey).where(EncryptionKey.key_id == key_id)
        )
        row = existing.scalar_one_or_none()

        now = datetime.now(timezone.utc).isoformat()

        if row is not None:
            row.encrypted_dek = encrypted_dek
            row.is_active = is_active
            row.rotated_at = now
        else:
            new_row = EncryptionKey(
                key_id=key_id,
                encrypted_dek=encrypted_dek,
                is_active=is_active,
                created_at=now,
            )
            session.add(new_row)

        await session.flush()
        logger.debug("DEK key_id=%s сохранён в БД (active=%s)", key_id, is_active)

    async def persist_rotation(
        self,
        session,
        new_key_id: int,
        new_encrypted_dek: str,
        old_key_id: int | None = None,
    ) -> None:
        """Сохраняет результат ротации в БД: деактивирует старый, сохраняет новый.

        Args:
            session: SQLAlchemy AsyncSession.
            new_key_id: ID нового ключа.
            new_encrypted_dek: новый DEK, зашифрованный KEK.
            old_key_id: ID старого ключа (для деактивации).
        """
        from sqlalchemy import update

        from src.db.models._encryption import EncryptionKey

        # Деактивируем старый ключ
        if old_key_id is not None:
            await session.execute(
                update(EncryptionKey)
                .where(EncryptionKey.key_id == old_key_id)
                .values(is_active=False)
            )

        # Сохраняем новый активный ключ
        await self.save_to_db(session, new_key_id, new_encrypted_dek, is_active=True)
        await session.commit()

    def get_all_key_ids(self) -> list[int]:
        """Возвращает список всех известных key_id (для отладки)."""
        return sorted(self._deks.keys())


# ── Глобальный singleton ──────────────────────────────────────────────

_rotation_manager: KeyRotationManager | None = None


def get_rotation_manager() -> KeyRotationManager | None:
    """Возвращает глобальный KeyRotationManager (если инициализирован)."""
    return _rotation_manager


def init_rotation_manager(kek: bytes, db_path: str | None = None) -> KeyRotationManager:
    """Инициализирует глобальный KeyRotationManager.

    Args:
        kek: KEK из settings.encryption_key.
        db_path: путь к БД (опционально).

    Returns:
        Инициализированный KeyRotationManager.
    """
    global _rotation_manager
    _rotation_manager = KeyRotationManager(kek, db_path)
    logger.info("KeyRotationManager инициализирован")
    return _rotation_manager
