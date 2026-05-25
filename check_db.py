"""Check database schema and data."""

import sqlite3

db = sqlite3.connect("data/app.db")
cur = db.cursor()

# Get table list
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [t[0] for t in cur.fetchall()]
print("Tables:", tables)

# For relevant tables, show schema and data
for name in tables:
    if any(kw in name.lower() for kw in ["session", "key", "setting", "user"]):
        cur.execute(f'SELECT sql FROM sqlite_master WHERE name="{name}"')
        print(f"\n=== {name} ===")
        print(cur.fetchone()[0])

        # Show data
        col_info = cur.execute(f'PRAGMA table_info("{name}")').fetchall()
        cols = [c[1] for c in col_info]
        cur.execute(f'SELECT * FROM "{name}"')
        rows = cur.fetchall()
        for row in rows:
            d = dict(zip(cols, row))
            # Truncate long fields
            for k, v in d.items():
                if isinstance(v, str) and len(v) > 60:
                    d[k] = v[:60] + "..."
            print(f"  {d}")

# Also check alembic version
print("\n=== alembic_version ===")
for row in cur.execute("SELECT * FROM alembic_version"):
    print(f"  {row}")

db.close()
print("\nDone.")
