"""Contact repository — Contact, ContactProfile, Folder, AllowedContact, watched peers."""

from __future__ import annotations

import json
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    AllowedContact,
    Contact,
    ContactProfile,
    Folder,
)
from src.db.repos.session_repo import _get_user_lock

logger = logging.getLogger(__name__)


async def upsert_contact(
    session: AsyncSession,
    user,
    *,
    peer_id: int,
    peer_kind: str,
    display_name: str,
    username: str | None = None,
    phone: str | None = None,
    is_bot: bool = False,
    is_archived: bool | None = None,
    folder_names: str | None = None,
) -> Contact:
    result = await session.execute(
        select(Contact).where(Contact.user_id == user.id, Contact.peer_id == peer_id)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        contact = Contact(
            user_id=user.id,
            peer_id=peer_id,
            peer_kind=peer_kind,
            is_bot=is_bot,
            is_archived=bool(is_archived) if is_archived is not None else False,
            display_name=display_name,
            username=username,
            phone=phone,
            folder_names=folder_names,
        )
        session.add(contact)
        await session.flush()
    else:
        contact.peer_kind = peer_kind
        contact.is_bot = is_bot
        if is_archived is not None:
            contact.is_archived = is_archived
        contact.display_name = display_name
        contact.username = username
        contact.phone = phone
        if folder_names is not None:
            contact.folder_names = folder_names
        await session.flush()
    return contact


async def list_contacts(
    session: AsyncSession,
    user,
    *,
    kinds: tuple[str, ...] | None = None,
    include_bots: bool = False,
    only_news_sources: bool = False,
    include_archived: bool | None = None,
) -> list[Contact]:
    # include_archived=None → берём решение из настроек пользователя
    if include_archived is None:
        include_archived = not user.settings.ignore_archived if user.settings else False

    query = select(Contact).where(Contact.user_id == user.id)
    if kinds:
        query = query.where(Contact.peer_kind.in_(kinds))
    if not include_bots:
        query = query.where(Contact.is_bot.is_(False))
    if only_news_sources:
        query = query.where(Contact.is_news_source.is_(True))
    if not include_archived:
        query = query.where(Contact.is_archived.is_(False))
    result = await session.execute(query)
    return list(result.scalars().all())


async def set_news_source(
    session: AsyncSession, user, peer_id: int, value: bool
) -> bool:
    result = await session.execute(
        select(Contact).where(Contact.user_id == user.id, Contact.peer_id == peer_id)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        return False
    contact.is_news_source = value
    await session.flush()
    return True


async def get_contact(session: AsyncSession, user, peer_id: int) -> Contact | None:
    result = await session.execute(
        select(Contact).where(Contact.user_id == user.id, Contact.peer_id == peer_id)
    )
    return result.scalar_one_or_none()


async def get_contacts_by_peer_ids(
    session: AsyncSession, user, peer_ids: set[int]
) -> dict[int, Contact]:
    """Batch-load contacts for many peer_ids in one query.

    Returns a mapping peer_id -> Contact for contacts that exist.
    """
    if not peer_ids:
        return {}
    result = await session.execute(
        select(Contact).where(
            Contact.user_id == user.id,
            Contact.peer_id.in_(list(peer_ids)),
        )
    )
    return {contact.peer_id: contact for contact in result.scalars().all()}


async def get_watched_peers(session: AsyncSession, user) -> set[int]:
    """Возвращает множество peer_id отслеживаемых чатов."""
    # Ensure settings relationship is loaded (avoids MissingGreenletError
    # when settings is a lazy-loaded relationship).
    await session.refresh(user, ["settings"])
    raw = user.settings.watched_peers
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
        return {int(p) for p in parsed}
    except (json.JSONDecodeError, TypeError, ValueError):
        return set()


async def is_peer_watched(session: AsyncSession, user, peer_id: int) -> bool:
    """Проверяет, отслеживается ли чат peer_id."""
    watched = await get_watched_peers(session, user)
    return peer_id in watched


async def add_watched_peer(session: AsyncSession, user, peer_id: int) -> None:
    """Добавляет peer_id в список отслеживаемых."""
    lock = _get_user_lock(user.id)
    async with lock:
        watched = await get_watched_peers(session, user)
        watched.add(peer_id)
        user.settings.watched_peers = json.dumps(sorted(watched))
        await session.flush()


async def remove_watched_peer(session: AsyncSession, user, peer_id: int) -> None:
    """Удаляет peer_id из списка отслеживаемых."""
    lock = _get_user_lock(user.id)
    async with lock:
        watched = await get_watched_peers(session, user)
        watched.discard(peer_id)
        user.settings.watched_peers = json.dumps(sorted(watched)) if watched else None
        await session.flush()


# ─── Pairing (AllowedContact) ─────────────────────────────────────────


async def is_contact_allowed(session: AsyncSession, telegram_id: int) -> bool:
    r = await session.execute(
        select(AllowedContact).where(AllowedContact.telegram_id == telegram_id)
    )
    return r.scalar_one_or_none() is not None


async def add_allowed_contact(
    session: AsyncSession, telegram_id: int, label: str | None = None
) -> None:
    session.add(AllowedContact(telegram_id=telegram_id, label=label))
    await session.flush()


async def remove_allowed_contact(session: AsyncSession, telegram_id: int) -> None:
    c = await session.get(AllowedContact, telegram_id)
    if c:
        await session.delete(c)
        await session.flush()


async def list_allowed_contacts(session: AsyncSession) -> list[int]:
    """Return all telegram_ids from the allowed_contacts table."""
    r = await session.execute(select(AllowedContact.telegram_id))
    return [row[0] for row in r.all()]


async def upsert_folders(session: AsyncSession, user, folders_data: list[dict]) -> int:
    """Сохраняет/обновляет папки. folders_data: [{'telegram_folder_id': int, 'title': str, 'emoji': str|None}]."""
    lock = _get_user_lock(user.id)
    async with lock:
        # Удалить старые папки этого пользователя
        await session.execute(delete(Folder).where(Folder.user_id == user.id))
        # Вставить новые
        saved = 0
        for f in folders_data:
            session.add(
                Folder(
                    user_id=user.id,
                    telegram_folder_id=f["telegram_folder_id"],
                    title=f["title"],
                    emoji=f.get("emoji"),
                )
            )
            saved += 1
        await session.flush()
    return saved


async def list_folders(session: AsyncSession, user) -> list[Folder]:
    """Возвращает список папок пользователя."""
    result = await session.execute(
        select(Folder).where(Folder.user_id == user.id).order_by(Folder.title)
    )
    return list(result.scalars().all())


# ─── ContactProfile CRUD ─────────────────────────────────────────────


async def upsert_contact_profile(
    session: AsyncSession,
    user,
    contact_id: int,
    **kwargs: object,
) -> ContactProfile:
    """Создаёт или обновляет профиль контакта.

    Переданные ``**kwargs`` применяются только если значение не None.

    Race-safety: if two concurrent calls hit the ``profile is None`` branch
    for the same ``(user_id, contact_id)`` pair, both INSERT and the loser
    raises ``IntegrityError`` on flush. We use a SAVEPOINT so only the
    failed INSERT is rolled back, not the entire transaction.
    """
    from sqlalchemy.exc import IntegrityError

    result = await session.execute(
        select(ContactProfile).where(
            ContactProfile.user_id == user.id,
            ContactProfile.contact_id == contact_id,
        )
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        filtered = {k: v for k, v in kwargs.items() if v is not None}
        try:
            async with session.begin_nested():
                profile = ContactProfile(
                    user_id=user.id, contact_id=contact_id, **filtered
                )
                session.add(profile)
                await session.flush()
        except IntegrityError:
            # Concurrent insert won — savepoint was rolled back automatically.
            # Re-fetch the row that the other call created.
            result = await session.execute(
                select(ContactProfile).where(
                    ContactProfile.user_id == user.id,
                    ContactProfile.contact_id == contact_id,
                )
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                # Race loser + concurrent insert also failed — retry fresh
                # Wrap in savepoint so a second IntegrityError doesn't blow up the caller's txn.
                try:
                    async with session.begin_nested():
                        profile = ContactProfile(
                            user_id=user.id, contact_id=contact_id, **filtered
                        )
                        session.add(profile)
                        await session.flush()
                except IntegrityError:
                    # Double-race: another caller's retry also just inserted.
                    # Re-fetch one last time.
                    result = await session.execute(
                        select(ContactProfile).where(
                            ContactProfile.user_id == user.id,
                            ContactProfile.contact_id == contact_id,
                        )
                    )
                    profile = result.scalar_one_or_none()
                    if profile is not None:
                        for k, v in kwargs.items():
                            if v is not None:
                                setattr(profile, k, v)
                    else:
                        # Triple-race edge case — both retries failed.
                        # Last resort: direct insert without contention context.
                        profile = ContactProfile(
                            user_id=user.id, contact_id=contact_id, **filtered
                        )
                        session.add(profile)
            else:
                for k, v in kwargs.items():
                    if v is not None:
                        setattr(profile, k, v)
    else:
        for k, v in kwargs.items():
            if v is not None:
                setattr(profile, k, v)
    await session.flush()
    return profile


async def get_contact_profile(
    session: AsyncSession,
    user,
    contact_id: int,
) -> ContactProfile | None:
    """Возвращает профиль контакта или None."""
    result = await session.execute(
        select(ContactProfile).where(
            ContactProfile.user_id == user.id,
            ContactProfile.contact_id == contact_id,
        )
    )
    return result.scalar_one_or_none()


async def list_contact_profiles(
    session: AsyncSession,
    user,
    limit: int = 50,
) -> list[ContactProfile]:
    """Возвращает профили контактов, отсортированные по близости (убывание)."""
    result = await session.execute(
        select(ContactProfile)
        .where(ContactProfile.user_id == user.id)
        .order_by(ContactProfile.closeness.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
