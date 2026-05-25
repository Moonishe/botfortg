import sqlite3

db = sqlite3.connect("data/app.db")
c = db.cursor()

# Notifications
c.execute("SELECT sql FROM sqlite_master WHERE name='notifications'")
print("NOTIFICATIONS SCHEMA:", c.fetchone()[0])
c.execute("SELECT * FROM notifications")
print("Columns:", [d[0] for d in c.description])
for r in c.fetchall():
    print(r)

print()
# Messages
c.execute("SELECT sql FROM sqlite_master WHERE name='messages'")
print("MESSAGES SCHEMA:", c.fetchone()[0])
c.execute("SELECT COUNT(*) FROM messages")
print("Message count:", c.fetchone()[0])

db.close()
