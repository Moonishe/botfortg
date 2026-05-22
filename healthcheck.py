"""Health check for Docker.

Connects to the database (SELECT 1), verifies key module imports,
and exits with status 0 on success, 1 on failure.
"""

import asyncio
import sys

from sqlalchemy import text


async def _check() -> bool:
    try:
        # 1. Config loads (validates env vars)
        from src.config import settings  # noqa: F401

        # 2. DB engine imports and connects
        from src.db.session import engine

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.scalar_one()

        # 3. Core modules resolve (no missing deps)
        import src.db.models  # noqa: F401
        import src.main  # noqa: F401

        return True
    except Exception as exc:
        print(f"HEALTHCHECK FAILED: {exc}", file=sys.stderr)
        return False


def main() -> None:
    success = asyncio.run(_check())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
