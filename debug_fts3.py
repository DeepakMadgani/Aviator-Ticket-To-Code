import sqlite3
conn = sqlite3.connect("C:/CC4E/.aviator/index.db")

# Check symbols columns
cols = conn.execute("PRAGMA table_info(symbols)").fetchall()
print("symbols cols:", [c[1] for c in cols])

# Check how symbols_fts links to symbols
# FTS5 content tables use rowid to link back
rows = conn.execute(
    "SELECT s.path, s.name FROM symbols_fts f "
    "JOIN symbols s ON s.rowid = f.rowid "
    "WHERE symbols_fts MATCH 'footer' LIMIT 5"
).fetchall()
print("FTS footer via join:", rows)

rows2 = conn.execute(
    "SELECT s.path, s.name FROM symbols_fts f "
    "JOIN symbols s ON s.rowid = f.rowid "
    "WHERE symbols_fts MATCH 'GenericConstants' LIMIT 5"
).fetchall()
print("FTS GenericConstants via join:", rows2)

# Also try: just fts matching 
rows3 = conn.execute(
    "SELECT rowid, name, qualified_name FROM symbols_fts WHERE symbols_fts MATCH 'GenericConstants' LIMIT 5"
).fetchall()
print("FTS GenericConstants rowids:", rows3)

# Check symbols rowid for GenericConstants
rows4 = conn.execute(
    "SELECT rowid, path, name FROM symbols WHERE name = 'GenericConstants' LIMIT 3"
).fetchall()
print("Symbols rowid GenericConstants:", rows4)
