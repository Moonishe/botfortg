"""Key repository — ApiKey, LlmKeySlot, LlmKeySlotModel."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    ApiKey,
    LlmKeySlot,
    LlmKeySlotModel,
    User,
)
from src.crypto import decrypt_async, encrypt_async

logger = logging.getLogger(__name__)


async def upsert_api_key(session: AsyncSession, user, provider: str, key: str) -> None:
    from src.db.repos.session_repo import _get_user_lock

    lock = _get_user_lock(user.id)
    async with lock:
        # Нормализация: поддерживается несколько ключей через запятую
        parts = [k.strip() for k in key.split(",") if k.strip()]
        if not parts:
            return
        normalized = ",".join(parts)
        result = await session.execute(
            select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == provider)
        )
        existing = result.scalar_one_or_none()
        enc = await encrypt_async(normalized)
        if existing is None:
            session.add(ApiKey(user_id=user.id, provider=provider, key_enc=enc))
        else:
            existing.key_enc = enc

        # Унификация: также сохраняем в LlmKeySlot (новое хранилище)
        # Каждый ключ из списка — отдельный слот
        existing_slots = await list_key_slots(session, user, provider=provider)
        existing_keys: set[str] = set()
        for s in existing_slots:
            try:
                existing_keys.add(await decrypt_async(s.key_enc))
            except Exception:
                continue

        for i, single_key in enumerate(parts):
            if single_key not in existing_keys:
                slot = LlmKeySlot(
                    user_id=user.id,
                    provider=provider,
                    purpose="main",
                    label=f"{provider}/main",
                    key_enc=await encrypt_async(single_key),
                    priority=i,
                )
                session.add(slot)
            else:
                # Ключ уже есть в LlmKeySlot — не дублируем
                pass

        await session.flush()


async def get_api_key(session: AsyncSession, user, provider: str) -> str | None:
    """Возвращает сохранённый ключ(и). Если ключей несколько — через запятую."""
    result = await session.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == provider)
    )
    row = result.scalar_one_or_none()
    return await decrypt_async(row.key_enc) if row is not None else None


async def get_api_keys(session: AsyncSession, user, provider: str) -> list[str]:
    """Возвращает список ключей для провайдера."""
    raw = await get_api_key(session, user, provider)
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


async def add_key_slot(
    session: AsyncSession,
    user,
    provider: str,
    key: str,
    *,
    purpose: str = "main",
    label: str | None = None,
    priority: int = 0,
    endpoint: str | None = None,
    model: str | None = None,
    category: str = "llm",
) -> tuple[LlmKeySlot, bool]:
    """Добавляет слот ключа.

    Возвращает (LlmKeySlot, is_new):
      - is_new=True  — слот создан впервые
      - is_new=False — ключ с таким же значением уже существует (слот #N)
    """
    from src.db.repos.session_repo import _get_user_lock

    lock = _get_user_lock(user.id)
    async with lock:
        # Проверка дубликатов: расшифровываем все существующие слоты пользователя
        # и сравниваем с новым ключом
        existing_slots = await list_key_slots(
            session, user, provider=provider, purpose=purpose
        )
        for existing in existing_slots:
            try:
                existing_key = await decrypt_async(existing.key_enc)
                if existing_key == key:
                    return existing, False
            except Exception:
                continue

        slot = LlmKeySlot(
            user_id=user.id,
            provider=provider,
            purpose=purpose,
            label=label,
            endpoint=endpoint,
            model=model,
            category=category,
            key_enc=await encrypt_async(key),
            priority=priority,
        )
        session.add(slot)
        await session.flush()
        return slot, True


async def list_key_slots(
    session: AsyncSession,
    user,
    provider: str | None = None,
    purpose: str | None = None,
) -> list[LlmKeySlot]:
    """Список слотов с фильтрацией."""
    q = select(LlmKeySlot).where(LlmKeySlot.user_id == user.id)
    if provider:
        q = q.where(LlmKeySlot.provider == provider)
    if purpose:
        q = q.where(LlmKeySlot.purpose == purpose)
    q = q.order_by(LlmKeySlot.priority.desc())
    r = await session.execute(q)
    return list(r.scalars().all())


async def get_active_keys(
    session: AsyncSession,
    user,
    provider: str,
    purpose: str = "main",
) -> list[LlmKeySlot]:
    """Активные (enabled, не в кулдауне) ключи для провайдера и назначения."""
    now = datetime.now(UTC)
    q = (
        select(LlmKeySlot)
        .where(
            LlmKeySlot.user_id == user.id,
            LlmKeySlot.provider == provider,
            LlmKeySlot.purpose == purpose,
            LlmKeySlot.enabled,
            or_(LlmKeySlot.cooldown_until.is_(None), LlmKeySlot.cooldown_until <= now),
        )
        .order_by(LlmKeySlot.priority.desc())
    )
    r = await session.execute(q)
    return list(r.scalars().all())


async def mark_key_failure(
    session: AsyncSession,
    slot_id: int,
    error_msg: str,
    cooldown_sec: int = 120,
) -> None:
    """Помечает ключ как упавший с кулдауном."""
    slot = await session.get(LlmKeySlot, slot_id)
    if slot:
        slot.failure_count = (slot.failure_count or 0) + 1
        slot.last_error = error_msg[:256]
        slot.last_error_at = datetime.now(UTC)
        slot.cooldown_until = datetime.now(UTC) + timedelta(
            seconds=cooldown_sec
        )
        await session.flush()


async def mark_key_used(session: AsyncSession, slot_id: int) -> None:
    """Инкремент счётчика использования."""
    slot = await session.get(LlmKeySlot, slot_id)
    if slot:
        slot.usage_count = (slot.usage_count or 0) + 1
        slot.cooldown_until = None
        slot.last_error = None
        await session.flush()


# ─── LlmKeySlotModel CRUD ───────────────────────────────────────────────


async def get_slot_models(session: AsyncSession, slot_id: int) -> list[LlmKeySlotModel]:
    """Получить все модели слота (включая выключенные).

    NOTE: caller must verify slot.user_id == owner.id before calling.
    """
    stmt = (
        select(LlmKeySlotModel)
        .where(LlmKeySlotModel.slot_id == slot_id)
        .order_by(LlmKeySlotModel.created_at)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def set_slot_models(
    session: AsyncSession, slot_id: int, model_names: list[str]
) -> None:
    """Заменить модели слота (удалить старые, добавить новые).

    NOTE: caller must verify slot.user_id == owner.id before calling.
    Idempotent: repeat calls produce same result. Transaction-level atomicity sufficient.
    """
    # Удаляем существующие
    await session.execute(
        delete(LlmKeySlotModel).where(LlmKeySlotModel.slot_id == slot_id)
    )
    # Добавляем новые
    for name in model_names:
        session.add(LlmKeySlotModel(slot_id=slot_id, model_name=name, enabled=True))
    await session.flush()


async def toggle_slot_model(
    session: AsyncSession, slot_id: int, model_name: str, enabled: bool
) -> bool:
    """Включить/выключить модель в слоте. Возвращает True если переключено.

    NOTE: caller must verify slot.user_id == owner.id before calling.
    """
    stmt = select(LlmKeySlotModel).where(
        LlmKeySlotModel.slot_id == slot_id,
        LlmKeySlotModel.model_name == model_name,
    )
    model = (await session.execute(stmt)).scalar_one_or_none()
    if model is not None:
        model.enabled = enabled
        return True
    return False


async def get_enabled_models(session: AsyncSession, slot_id: int) -> list[str]:
    """Получить список имён enabled-моделей для слота.

    NOTE: caller must verify slot.user_id == owner.id before calling.
    """
    models = await get_slot_models(session, slot_id)
    return [m.model_name for m in models if m.enabled]


async def get_key_slot(
    session: AsyncSession, slot_id: int, user: User
) -> LlmKeySlot | None:
    """Возвращает слот ключа по ID с проверкой владения."""
    slot = await session.get(LlmKeySlot, slot_id)
    if slot is None or slot.user_id != user.id:
        return None
    return slot


async def delete_key_slot(session: AsyncSession, slot_id: int, user: User) -> bool:
    """Удаляет слот ключа с проверкой владения."""
    slot = await session.get(LlmKeySlot, slot_id)
    if slot is None or slot.user_id != user.id:
        return False
    await session.delete(slot)
    await session.flush()
    return True


async def add_key_slot_raw(
    session: AsyncSession,
    user: User,
    provider: str,
    key_enc: str,
    *,
    purpose: str = "main",
    model: str = "",
    endpoint: str = "",
    category: str = "llm",
    label: str = "",
    priority: int = 0,
    enabled: bool = True,
) -> LlmKeySlot:
    """Добавляет слот с уже зашифрованным ключом (без повторного шифрования).

    Используется для импорта конфигурации.
    """
    slot = LlmKeySlot(
        user_id=user.id,
        provider=provider,
        purpose=purpose,
        model=model,
        endpoint=endpoint,
        category=category,
        label=label,
        priority=priority,
        enabled=enabled,
        key_enc=key_enc,
    )
    session.add(slot)
    await session.flush()
    return slot
