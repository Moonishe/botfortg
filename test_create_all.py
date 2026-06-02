"""Test just Base.metadata.create_all on a fresh DB."""

import os
import sys
import time

os.environ["API_ID"] = "12345"
os.environ["API_HASH"] = "0123456789abcdef0123456789abcdef"
os.environ["BOT_TOKEN"] = "test:token"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./_create_all_test.db"
os.environ["PYTHONUNBUFFERED"] = "1"

try:
    os.remove("./_create_all_test.db")
except FileNotFoundError:
    pass

print("Importing models...", flush=True)
sys.path.insert(0, ".")
sys.path.insert(0, "./src")
from db.models import Base

print(f"Number of tables: {len(Base.metadata.tables)}", flush=True)
for t in Base.metadata.tables:
    print(f"  {t}", flush=True)

print("\nCreating engine...", flush=True)
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine("sqlite+aiosqlite:///./_create_all_test.db", future=True)

import asyncio


async def go():
    print("Running create_all...", flush=True)
    t0 = time.time()
    async with engine.begin() as conn:
        from sqlalchemy.orm import declarative_base

        await conn.run_sync(Base.metadata.create_all)
    print(f"create_all done in {time.time() - t0:.1f}s", flush=True)
    await engine.dispose()


asyncio.run(go())
print("FINISHED", flush=True)
