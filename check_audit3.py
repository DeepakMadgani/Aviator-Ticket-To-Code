import sqlite3

conn = sqlite3.connect(r'C:\CC4E\.aviator\index.db')
cur = conn.cursor()

print("=== 1. ghsHelpVersion in symbols? ===")
cur.execute("SELECT path, name, kind FROM symbols WHERE name LIKE '%helpVersion%' OR name LIKE '%ghsHelp%'")
print(cur.fetchall())

print("\n=== 2. app.component.ts symbols (first 10) ===")
cur.execute("SELECT name, kind FROM symbols WHERE path LIKE '%app.component.ts%' AND path NOT LIKE '%spec%' LIMIT 10")
print(cur.fetchall())

print("\n=== 3. Does FTS index contain '260200'? ===")
try:
    cur.execute("SELECT path, name FROM symbols WHERE path LIKE '%app.component%' AND name LIKE '%260%'")
    print("Direct LIKE:", cur.fetchall())
except Exception as e:
    print("Error:", e)

try:
    cur.execute("SELECT path, name FROM symbols_fts JOIN symbols ON symbols_fts.rowid = symbols.rowid WHERE symbols_fts MATCH '260200'")
    print("FTS 260200:", cur.fetchall()[:5])
except Exception as e:
    print("FTS error:", e)

print("\n=== 4. What symbols does app.component.ts export? ===")
cur.execute("SELECT name, kind, start_line FROM symbols WHERE path = 'xchange-ui/src/app/app.component.ts' ORDER BY start_line LIMIT 20")
for row in cur.fetchall():
    print(f"  L{row[2]} {row[1]}: {row[0]}")

print("\n=== 5. Are there ANY edges involving app.component.ts? ===")
cur.execute("SELECT src_id, dst_id, kind FROM edges WHERE src_id LIKE '%app.component%' OR dst_id LIKE '%app.component%' LIMIT 10")
edges = cur.fetchall()
print(f"  {len(edges)} edges")
for e in edges[:5]:
    print(f"  {e[2]}: {e[0]} → {e[1]}")

print("\n=== 6. Does jheader share any edges with app.component? ===")
cur.execute("""
    SELECT e.src_id, e.dst_id, e.kind 
    FROM edges e 
    WHERE (e.src_id LIKE '%jheader%' AND e.dst_id LIKE '%app.component%')
       OR (e.src_id LIKE '%app.component%' AND e.dst_id LIKE '%jheader%')
""")
print("jheader <-> app.component edges:", cur.fetchall())

print("\n=== 7. What keywords does discovery use for version ticket? ===")
# This shows what the keyword search would match
cur.execute("SELECT path FROM files WHERE path LIKE '%version%' OR path LIKE '%Version%' LIMIT 10")
print("Files with 'version' in path:", cur.fetchall())

conn.close()
