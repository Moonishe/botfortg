"""Test contact_repo upsert and race safety."""

import asyncio

import pytest
from sqlalchemy import text

from src.db.models._contacts import Contact, ContactProfile
from src.db.repos.contact_repo import upsert_contact, upsert_contact_profile
from src.db.repo import get_or_create_user
from src.db.session import get_session


pytestmark = pytest.mark.usefixtures("_db_init")


@pytest.fixture(autouse=True)
async def _ensure_contact_profile_constraint():
    """Ensure the unique constraint exists (production migration adds it)."""
    async with get_session() as session:
        await session.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_contact_profile_user_contact "
                "ON contact_profiles (user_id, contact_id)"
            )
        )
        await session.commit()
    yield


@pytest.mark.asyncio
async def test_upsert_contact_profile_creates_row():
    """Basic upsert: creates a ContactProfile with kwargs."""
    async with get_session() as session:
        user = await get_or_create_user(session, 5550001)
        await session.commit()

        profile = await upsert_contact_profile(
            session,
            user,
            contact_id=111222,
            closeness=0.8,
            closeness_label="friend",
        )
        await session.commit()

        assert profile is not None
        assert profile.user_id == user.id
        assert profile.contact_id == 111222
        assert profile.closeness == 0.8
        assert profile.closeness_label == "friend"


@pytest.mark.asyncio
async def test_upsert_contact_profile_updates_existing():
    """Upsert updates an existing profile without IntegrityError."""
    async with get_session() as session:
        user = await get_or_create_user(session, 5550002)
        await session.commit()

        # First upsert
        profile1 = await upsert_contact_profile(
            session,
            user,
            contact_id=222333,
            closeness=0.3,
        )
        await session.commit()

        # Second upsert with different kwargs
        profile2 = await upsert_contact_profile(
            session,
            user,
            contact_id=222333,
            closeness=0.9,
            closeness_label="colleague",
        )
        await session.commit()

        assert profile2.id == profile1.id  # same row
        assert profile2.closeness == 0.9
        assert profile2.closeness_label == "colleague"


@pytest.mark.asyncio
async def test_upsert_contact_profile_empty_kwargs():
    """Empty kwargs should create a row with defaults (no crash)."""
    async with get_session() as session:
        user = await get_or_create_user(session, 5550003)
        await session.commit()

        profile = await upsert_contact_profile(
            session,
            user,
            contact_id=333444,
        )
        await session.commit()

        assert profile is not None
        assert profile.user_id == user.id
        assert profile.contact_id == 333444
        # Defaults from model
        assert profile.closeness == 0.5


@pytest.mark.asyncio
async def test_upsert_contact_profile_race_safe():
    """Concurrent upserts for same (user_id, contact_id) must not fail."""
    async with get_session() as session:
        user = await get_or_create_user(session, 5550004)
        await session.commit()
        user_id = user.id

    async def _upsert_with_delay(kwargs: dict):
        async with get_session() as s:
            from src.db.models._base import User

            u = await s.get(User, user_id)
            return await upsert_contact_profile(s, u, contact_id=444555, **kwargs)

    # Run 3 concurrent upserts
    results = await asyncio.gather(
        _upsert_with_delay({"closeness": 0.1}),
        _upsert_with_delay({"closeness": 0.5}),
        _upsert_with_delay({"closeness": 0.9}),
    )

    # All should return a valid profile
    for r in results:
        assert r is not None
        assert r.user_id == user_id
        assert r.contact_id == 444555

    # Only one row should exist
    async with get_session() as session:
        from sqlalchemy import select

        cnt_result = await session.execute(
            select(ContactProfile).where(
                ContactProfile.user_id == user_id,
                ContactProfile.contact_id == 444555,
            )
        )
        rows = cnt_result.scalars().all()
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"


@pytest.mark.asyncio
async def test_upsert_contact_creates_row():
    """Basic upsert_contact: creates a Contact."""
    async with get_session() as session:
        user = await get_or_create_user(session, 5550011)
        await session.commit()

        contact = await upsert_contact(
            session,
            user,
            peer_id=999888,
            peer_kind="user",
            display_name="Alice",
        )
        await session.commit()

        assert contact is not None
        assert contact.user_id == user.id
        assert contact.peer_id == 999888


@pytest.mark.asyncio
async def test_upsert_contact_race_safe():
    """Concurrent upsert_contact for same (user_id, peer_id) must not fail."""
    async with get_session() as session:
        user = await get_or_create_user(session, 5550012)
        await session.commit()
        user_id = user.id

    async def _upsert():
        async with get_session() as s:
            from src.db.models._base import User

            u = await s.get(User, user_id)
            return await upsert_contact(
                s, u, peer_id=777666, peer_kind="user", display_name="Bob"
            )

    results = await asyncio.gather(
        _upsert(),
        _upsert(),
        _upsert(),
    )

    for r in results:
        assert r is not None
        assert r.user_id == user_id
        assert r.peer_id == 777666

    async with get_session() as session:
        from sqlalchemy import select

        cnt_result = await session.execute(
            select(Contact).where(
                Contact.user_id == user_id,
                Contact.peer_id == 777666,
            )
        )
        rows = cnt_result.scalars().all()
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
