"""Test full migration with echo to see SQL."""

import os
import sys
import time

os.environ["API_ID"] = "12345"
os.environ["API_HASH"] = "0123456789abcdef0123456789abcdef"
os.environ["BOT_TOKEN"] = "test:token"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./_initial_test.db"
os.environ["PYTHONUNBUFFERED"] = "1"

try:
    os.remove("./_initial_test.db")
except FileNotFoundError:
    pass

import logging

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

from alembic.config import Config
from alembic import command

cfg = Config("./alembic.ini")
print("\n=== Running upgrade head ===", flush=True)
t0 = time.time()
try:
    command.upgrade(cfg, "head")
    print(f"\n=== DONE head in {time.time() - t0:.1f}s ===", flush=True)
except Exception as e:
    print(f"\n=== FAILED: {e} ===", flush=True)
    import traceback

    traceback.print_exc()
