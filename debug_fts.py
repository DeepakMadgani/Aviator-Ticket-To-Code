import sqlite3
conn = sqlite3.connect("C:/CC4E/.aviator/index.db")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", [t[0] for t in tables])
try:
    rows = conn.execute("SELECT path FROM files_fts WHERE files_fts MATCH 'footer' LIMIT 3").fetchall()
    print("FTS footer:", rows)
except Exception as e:
    print("FTS error:", e)
rows = conn.execute("SELECT path, name FROM symbols WHERE LOWER(name) LIKE '%genericconstants%' LIMIT 5").fetchall()
print("Symbol GenericConstants:", rows)
rows = conn.execute("SELECT path, name FROM symbols WHERE LOWER(name) LIKE '%reviewers%' LIMIT 5").fetchall()
print("Symbol reviewers:", rows[:3])
