import sqlite3
conn = sqlite3.connect("C:/CC4E/.aviator/index.db")
cols = conn.execute("PRAGMA table_info(symbols_fts)").fetchall()
print("symbols_fts cols:", cols)

rows = conn.execute("SELECT path, name FROM symbols_fts WHERE symbols_fts MATCH 'footer' LIMIT 5").fetchall()
print("FTS footer:", rows)

rows2 = conn.execute("SELECT path, name FROM symbols_fts WHERE symbols_fts MATCH 'GenericConstants' LIMIT 5").fetchall()
print("FTS GenericConstants:", rows2)

try:
    rows3 = conn.execute("SELECT path, name FROM symbols_fts WHERE symbols_fts MATCH '26.2' LIMIT 3").fetchall()
    print("FTS 26.2:", rows3)
except Exception as e:
    print("FTS 26.2 error:", e)

# Check LIKE fallback for symbols
rows4 = conn.execute("SELECT path, name FROM symbols WHERE LOWER(name) LIKE '%footer%' LIMIT 5").fetchall()
print("Symbol LIKE footer:", rows4)
