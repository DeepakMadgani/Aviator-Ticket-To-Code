import sqlite3

conn = sqlite3.connect(r'C:\CC4E\.aviator\index.db')
cur = conn.cursor()

# Check actual schema
cur.execute("PRAGMA table_info(symbols)")
print("symbols schema:", cur.fetchall())

cur.execute("PRAGMA table_info(files)")
print("files schema:", [(r[1], r[2]) for r in cur.fetchall()])

# Get symbols columns to find the right FK
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("tables:", cur.fetchall())

conn.close()
