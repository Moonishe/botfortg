import sqlite3, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
db = sqlite3.connect("data/app.db")
c = db.cursor()

c.execute("SELECT * FROM notifications")
print("Columns:", [d[0] for d in c.description])
for r in c.fetchall():
    print(repr(r))

c.execute("SELECT COUNT(*) FROM messages")
print("Message count:", c.fetchone()[0])

db.close()
