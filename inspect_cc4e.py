import sqlite3  # graph edge inspection
conn = sqlite3.connect(r'C:\CC4E\.aviator\index.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print('=== JATO/footer symbols ===')
rows = cur.execute(
    "SELECT name, path FROM symbols WHERE lower(name) LIKE '%jato%' OR lower(name) LIKE '%footer%' LIMIT 20"
).fetchall()
for r in rows:
    print(f'  {r["name"]:40s}  {r["path"]}')

print()
print('=== Files with jato/footer in path ===')
rows = cur.execute(
    "SELECT path FROM files WHERE lower(path) LIKE '%jato%' OR lower(path) LIKE '%footer%' LIMIT 20"
).fetchall()
for r in rows:
    print(f'  {r["path"]}')

print()
print('=== xchange-ui: what file types exist? ===')
rows = cur.execute(
    "SELECT DISTINCT language, COUNT(*) as cnt FROM files WHERE path LIKE 'xchange-ui/%' GROUP BY language"
).fetchall()
for r in rows:
    print(f'  {r["language"]:20s}: {r["cnt"]} files')

print()
print('=== xchange-ui sample files ===')
rows = cur.execute(
    "SELECT path FROM files WHERE path LIKE 'xchange-ui/%' LIMIT 20"
).fetchall()
for r in rows:
    print(f'  {r["path"]}')

conn.close()
