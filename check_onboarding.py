"""Проверка онбординга через прямое обращение к БД."""

import sqlite3
import sys

db = sqlite3.connect("data/app.db")
cur = db.cursor()

# User
print("=== Users ===")
for row in cur.execute("SELECT id, telegram_id, created_at FROM users"):
    print(f"  id={row[0]}, tg_id={row[1]}, created={row[2]}")

# Telegram sessions
print("\n=== Telegram Sessions ===")
for row in cur.execute(
    "SELECT id, user_id, telegram_userid, phone, session_string[:50] FROM telegram_sessions"
):
    print(
        f"  id={row[0]}, user_id={row[1]}, tg_uid={row[2]}, phone={row[3]}, has_session={bool(row[4])}"
    )

# LLM Key Slots
print("\n=== LLM Key Slots ===")
for row in cur.execute("SELECT id, user_id, provider, api_key FROM llm_key_slots"):
    print(f"  id={row[0]}, user_id={row[1]}, provider={row[2]}, has_key={bool(row[3])}")

# User settings
print("\n=== User Settings ===")
try:
    for row in cur.execute("SELECT id, user_id, timezone FROM user_settings"):
        print(f"  id={row[0]}, user_id={row[1]}, tz={row[2]}")
except:
    print("  no user_settings table or columns")

db.close()
print("\nDone.")
