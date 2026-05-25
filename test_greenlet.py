"""Test if lazy loading works in async context - NO Unicode."""

import asyncio, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")


async def test():
    from src.db.session import get_session
    from src.db.repos.session_repo import get_or_create_user
    from src.config import settings

    print(f"Testing with tg_id={settings.owner_telegram_id}")

    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)
        print(f"User: id={owner.id}, tg_id={owner.telegram_id}")

        # Test lazy loading of session
        try:
            s = owner.session
            print(f"Session lazy load: OK -> {s is not None}")
        except Exception as e:
            print(f"Session lazy load: FAILED -> {type(e).__name__}: {e}")

        # Test lazy loading of key_slots
        try:
            ks = owner.key_slots
            print(f"KeySlots lazy load: OK -> len={len(ks)}")
        except Exception as e:
            print(f"KeySlots lazy load: FAILED -> {type(e).__name__}: {e}")

        # Test settings
        try:
            tz = owner.settings.timezone
            print(f"Settings access: OK -> tz={tz}")
        except Exception as e:
            print(f"Settings access: FAILED -> {type(e).__name__}: {e}")

    # Test v2 - with selectinload
    print("\n--- With selectinload ---")
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from src.db.models import User

    async with get_session() as session:
        result = await session.execute(
            select(User)
            .where(User.telegram_id == settings.owner_telegram_id)
            .options(
                selectinload(User.session),
                selectinload(User.key_slots),
                selectinload(User.settings),
            )
        )
        owner = result.scalar_one_or_none()
        if owner:
            s = owner.session
            print(f"Session: {s is not None}")
            ks = owner.key_slots
            print(f"KeySlots: len={len(ks)}")
            tz = owner.settings.timezone
            print(f"Settings: tz={tz}")


asyncio.run(test())
