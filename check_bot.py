"""Проверка состояния бота — онбординг, БД, и т.д."""

import asyncio
import sys

sys.path.insert(0, ".")


async def check():
    from src.config import settings
    from src.db.session import get_session
    from src.db.repo import get_or_create_user

    print(f"Owner telegram_id: {settings.owner_telegram_id}")
    print(f"Bot token prefix: {settings.bot_token[:20]}...")

    async with get_session() as session:
        owner = await get_or_create_user(session, settings.owner_telegram_id)
        print(f"\n=== User DB ===")
        print(f"  id: {owner.id}")
        print(f"  telegram_id: {owner.telegram_id}")
        print(f"  created_at: {owner.created_at}")

        # Session
        try:
            has_session = owner.session is not None
            print(f"\n  has session: {has_session}")
            if has_session:
                print(f"  session user_id: {owner.session.telegram_userid}")
                print(f"  session phone: {owner.session.phone}")
        except Exception as e:
            print(f"  session error: {e}")

        # LLM keys
        try:
            print(f"  key slots count: {len(owner.key_slots)}")
            for i, ks in enumerate(owner.key_slots):
                print(
                    f"    slot {i}: provider={ks.provider}, key={'***' + ks.api_key[-4:] if ks.api_key else 'None'}"
                )
        except Exception as e:
            print(f"  key_slots error: {e}")

        # Settings / timezone
        try:
            tz = owner.settings.timezone if owner.settings else "NO SETTINGS"
            print(f"  timezone: {tz}")
        except Exception as e:
            print(f"  settings error: {e}")

    # Check onboarding
    from src.bot.filters import get_onboarding_phase, is_onboarded

    phase = await get_onboarding_phase(settings.owner_telegram_id)
    onboarded = await is_onboarded(settings.owner_telegram_id)
    print(f"\n=== Onboarding ===")
    print(f"  phase: {phase}")
    print(f"  is_onboarded: {onboarded}")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(check())
