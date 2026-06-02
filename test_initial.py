"""Run all migrations to head."""

import os
import sys

os.environ["API_ID"] = "12345"
os.environ["API_HASH"] = "0123456789abcdef0123456789abcdef"
os.environ["BOT_TOKEN"] = "test:token"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./_initial_test.db"
os.environ["PYTHONUNBUFFERED"] = "1"

import logging

logging.basicConfig(level=logging.WARNING)

from alembic.config import Config
from alembic import command

cfg = Config("./alembic.ini")
print("Running upgrade head...", flush=True)
try:
    command.upgrade(cfg, "head")
    print("DONE head", flush=True)
except Exception as e:
    print(f"FAILED: {e}", flush=True)
    import traceback

    traceback.print_exc()
