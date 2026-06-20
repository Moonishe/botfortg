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

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, UTC
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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
                "Сгенерируйте: python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            )
        self._kek_bytes: bytes = kek
        self._kek_fernet: Fernet = Fernet(kek)
        self._db_path: str | None = db_path

        # In-memory кэш: key_id → DEK (bytes, уже расшифрованный)
        self._deks: dict[int, bytes] = {}
        # ID активного DEK
        self._active_key_id: int | None = None
        # Защита от конкурентной ротации (только один rotate за раз)
        self._lock = asyncio.Lock()
        # Максимальное число DEK'ов в in-memory кэше.
        # При превышении — LRU-эвикция: удаляется самый старый (минимальный key_id),
        # кроме активного. get_dek() возвращает только из кэша; при cache miss
        # вызывайте load_from_db() для перезагрузки из БД.
        self._MAX_CACHED_DEKS: int = 10

    @property
    def active_dek(self) -> bytes:
        """Текущий активный DEK для шифрования новых данных.

        Ни при каких обстоятельствах не возвращает KEK — KEK должен
        использоваться только для шифрования/расшифровки DEK'ов, но не данных.

        Raises:
            RuntimeError: если DEK не загружен (БД не инициализирована,
                не вызван load_from_db() или create_initial_dek()).

        Returns:
            32-байтовый ключ (urlsafe-base64 encoded) — активный DEK.
        """
        if self._active_key_id is not None and self._active_key_id in self._deks:
            return self._deks[self._active_key_id]

        raise RuntimeError(
            "active_dek: DEK не загружен. Вызовите load_from_db() или "
            "create_initial_dek() перед использованием active_dek. "
            "KEK не должен использоваться для шифрования данных — "
            "только для шифрования/расшифровки DEK'ов."
        )

    @property
    def dek_for_decryption(self) -> bytes:
        """DEK для расшифровки (с KEK-fallback для обратной совместимости).

        Используется ТОЛЬКО для расшифровки legacy-данных, которые
        были зашифрованы напрямую KEK до внедрения DEK-ротации.

        Returns:
            DEK если загружен, иначе KEK (только для расшифровки).
        """
        if self._active_key_id is not None and self._active_key_id in self._deks:
            return self._deks[self._active_key_id]
        logger.warning(
            "dek_for_decryption: DEK не загружен — возвращаю KEK как fallback "
            "(только для расшифровки legacy-данных)"
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

    def _evict_lru(self, protect_key_id: int | None, log_prefix: str) -> None:
        """LRU eviction: remove oldest non-protected keys until within cap."""
        if len(self._deks) <= self._MAX_CACHED_DEKS:
            return
        evict_candidates = sorted(k for k in self._deks if k != protect_key_id)
        for old_id in evict_candidates:
            self._deks.pop(old_id, None)
            logger.debug("%s DEK key_id=%s", log_prefix, old_id)
            if len(self._deks) <= self._MAX_CACHED_DEKS:
                break

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

    async def _rotate_unlocked(
        self,
        re_encrypt_callback: Callable[[bytes, bytes], Awaitable[Any]] | None = None,
    ) -> int:
        """Внутренняя ротация без захвата лока (caller должен держать self._lock)."""
        old_dek = self.active_dek
        old_key_id = self._active_key_id

        new_dek = self._generate_dek()
        new_key_id = (old_key_id if old_key_id is not None else 0) + 1
        # NOTE: key_id вычисляется in-memory; при конкурентной ротации
        # между инстансами коллизия разрешается в save_to_db() через
        # IntegrityError → автоинкремент SQLite.

        # Сохраняем новый DEK в in-memory кэш
        self._deks[new_key_id] = new_dek
        self._active_key_id = new_key_id

        # LRU eviction: если кэш переполнен — удаляем самый старый неактивный ключ
        self._evict_lru(new_key_id, "LRU eviction (cache overflow)")

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
                    "Перешифрование данных завершено: %s → %s",
                    old_key_id,
                    new_key_id,
                )
            except Exception:
                logger.exception(
                    "Ошибка при перешифровании данных (старый DEK сохранён, "
                    "новый активен, часть данных может быть под старым ключом)"
                )

        return new_key_id

    async def rotate(
        self,
        re_encrypt_callback: Callable[[bytes, bytes], Awaitable[Any]] | None = None,
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

        Note:
            Для атомарной ротации с сохранением в БД используйте
            :meth:`rotate_and_persist` — она откатывает in-memory состояние
            при неудаче persist.
        """
        async with self._lock:
            return await self._rotate_unlocked(re_encrypt_callback=re_encrypt_callback)

    async def rotate_and_persist(
        self,
        session: AsyncSession,
        re_encrypt_callback: Callable[[bytes, bytes], Awaitable[Any]] | None = None,
    ) -> int:
        """Атомарная ротация: обновляет in-memory состояние и БД.

        Если сохранение в БД не удалось, in-memory состояние откатывается
        к предыдущему активному ключу.

        Args:
            session: SQLAlchemy AsyncSession.
            re_encrypt_callback: async callable(old_dek, new_dek) -> None.

        Returns:
            ID нового активного ключа.

        Raises:
            Exception: пробрасывается из persist_rotation.
        """
        async with self._lock:
            old_key_id = self._active_key_id
            new_key_id = await self._rotate_unlocked(
                re_encrypt_callback=re_encrypt_callback
            )
            new_dek = self._deks.get(new_key_id)
            if new_dek is None:
                # Внутренняя ошибка: rotate вернул id, которого нет в кэше
                self._active_key_id = old_key_id
                raise RuntimeError(f"New DEK key_id={new_key_id} missing after rotate")
            try:
                actual_key_id = await self.persist_rotation(
                    session,
                    new_key_id,
                    self._encrypt_dek(new_dek),
                    old_key_id=old_key_id,
                )
            except Exception:
                # Откатываем in-memory состояние, чтобы не рассинхронизоваться с БД
                self._active_key_id = old_key_id
                self._deks.pop(new_key_id, None)
                logger.exception(
                    "DEK rotation persist failed — in-memory state rolled back"
                )
                raise

            # Если автоинкремент изменил key_id — синхронизируем in-memory кэш
            if actual_key_id != new_key_id:
                self._deks.pop(new_key_id, None)
                self._deks[actual_key_id] = new_dek
                self._active_key_id = actual_key_id
                logger.info(
                    "key_id скорректирован автоинкрементом: %s → %s",
                    new_key_id,
                    actual_key_id,
                )
            return actual_key_id

    async def load_from_db(self, session: AsyncSession) -> None:
        """Загружает существующие DEK'и из БД в in-memory кэш.

        Args:
            session: SQLAlchemy AsyncSession.
        """
        async with self._lock:
            await self._load_from_db_unlocked(session)

    async def _load_from_db_unlocked(self, session: AsyncSession) -> None:
        """Внутренняя загрузка без захвата лока."""
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

        # Enforce pool cap: evict oldest non-active keys if DB had more
        # keys than _MAX_CACHED_DEKS (otherwise the cap is only enforced
        # on rotation, leaving the pool oversized after initial load).
        self._evict_lru(self._active_key_id, "DB-load LRU eviction (pool overflow)")

        logger.info(
            "Загружено DEK'ов из БД: %d, активный: %s",
            len(self._deks),
            self._active_key_id,
        )

    async def create_initial_dek(self, session: AsyncSession) -> int | None:
        """Создаёт первый DEK при старте, если в БД нет активного ключа.

        Возвращает ID созданного ключа или None, если активный ключ уже есть.
        Потокобезопасно: держит self._lock.
        """
        async with self._lock:
            await self._load_from_db_unlocked(session)
            if self._active_key_id is not None:
                return None

            from sqlalchemy import func, select

            from src.db.models._encryption import EncryptionKey

            max_result = await session.execute(select(func.max(EncryptionKey.key_id)))
            max_id = max_result.scalar()
            key_id = (max_id or 0) + 1

            new_dek = self._generate_dek()
            self._deks[key_id] = new_dek
            self._active_key_id = key_id

            actual_key_id = await self.save_to_db(
                session, key_id, self._encrypt_dek(new_dek), is_active=True
            )
            # Если автоинкремент скорректировал key_id — синхронизируем кэш
            if actual_key_id != key_id:
                self._deks.pop(key_id, None)
                self._deks[actual_key_id] = new_dek
                self._active_key_id = actual_key_id
                logger.info(
                    "Начальный DEK: key_id скорректирован: %s → %s",
                    key_id,
                    actual_key_id,
                )
            logger.info("Начальный DEK создан (key_id=%s)", actual_key_id)
            return actual_key_id

    async def save_to_db(
        self,
        session: AsyncSession,
        key_id: int,
        encrypted_dek: str,
        is_active: bool = False,
    ) -> int:
        """Сохраняет DEK (зашифрованный KEK) в БД.

        Если key_id уже занят (race condition), использует автоинкремент
        SQLite для генерации нового ID и обновляет in-memory кэш.

        **Важно:** метод должен вызываться внутри активной транзакции —
        использует SAVEPOINT (session.begin_nested()) для изоляции отката
        при коллизии key_id.

        Args:
            session: SQLAlchemy AsyncSession.
            key_id: желаемый идентификатор ключа (может быть переопределён
                автоинкрементом при коллизии).
            encrypted_dek: Fernet-токен (DEK, зашифрованный KEK).
            is_active: является ли этот ключ активным.

        Returns:
            Фактический key_id, присвоенный записи (может отличаться
            от переданного при коллизии).
        """
        from src.db.models._encryption import EncryptionKey
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        # Проверяем, существует ли уже запись
        existing = await session.execute(
            select(EncryptionKey).where(EncryptionKey.key_id == key_id)
        )
        row = existing.scalar_one_or_none()

        now = datetime.now(UTC)

        if row is not None:
            row.encrypted_dek = encrypted_dek
            row.is_active = is_active
            row.rotated_at = now
            await session.flush()
            logger.debug("DEK key_id=%s обновлён в БД (active=%s)", key_id, is_active)
            return key_id

        # Пробуем вставить с заданным key_id
        try:
            async with session.begin_nested():  # SAVEPOINT — изолирует откат
                new_row = EncryptionKey(
                    key_id=key_id,
                    encrypted_dek=encrypted_dek,
                    is_active=is_active,
                    created_at=now,
                )
                session.add(new_row)
                await session.flush()
            actual_key_id = key_id
        except IntegrityError:
            # Коллизия key_id (race condition между инстансами) —
            # используем автоинкремент SQLite (SAVEPOINT откачен)
            logger.warning(
                "Коллизия key_id=%s при сохранении DEK — использую автоинкремент",
                key_id,
            )
            new_row = EncryptionKey(
                encrypted_dek=encrypted_dek,
                is_active=is_active,
                created_at=now,
            )
            session.add(new_row)
            await session.flush()
            actual_key_id = new_row.key_id
            # Обновляем in-memory кэш: удаляем старый key_id, добавляем новый
            self._deks.pop(key_id, None)
            self._deks[actual_key_id] = self._decrypt_dek(encrypted_dek)
            if is_active:
                self._active_key_id = actual_key_id
            logger.info(
                "DEK key_id=%s сохранён через автоинкремент (active=%s, "
                "запрошенный key_id=%s)",
                actual_key_id,
                is_active,
                key_id,
            )

        logger.debug(
            "DEK key_id=%s сохранён в БД (active=%s)", actual_key_id, is_active
        )
        return actual_key_id

    async def persist_rotation(
        self,
        session: AsyncSession,
        new_key_id: int,
        new_encrypted_dek: str,
        old_key_id: int | None = None,
    ) -> int:
        """Сохраняет результат ротации в БД: деактивирует старый, сохраняет новый.

        Args:
            session: SQLAlchemy AsyncSession.
            new_key_id: ожидаемый ID нового ключа.
            new_encrypted_dek: новый DEK, зашифрованный KEK.
            old_key_id: ID старого ключа (для деактивации).

        Returns:
            Фактический key_id нового ключа (может отличаться от new_key_id
            при автоинкрементном разрешении коллизии).
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

        # Сохраняем новый активный ключ (возвращает фактический key_id)
        actual_key_id = await self.save_to_db(
            session, new_key_id, new_encrypted_dek, is_active=True
        )
        await session.commit()
        return actual_key_id


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
