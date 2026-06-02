"""Test only the merge migration."""

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
# Start at d7e8f9a0b1c2, then merge with z9y8x7w6v5u4 -> 70f3b0b35097
print("Upgrading to d7e8f9a0b1c2...", flush=True)
command.upgrade(cfg, "d7e8f9a0b1c2")
print("Now upgrading to z9y8x7w6v5u4...", flush=True)
# Use stamp to set the version
command.stamp(cfg, "z9y8x7w6v5u4")
print("Now upgrading to head (merge)...", flush=True)
command.upgrade(cfg, "head")
print("DONE head", flush=True)
