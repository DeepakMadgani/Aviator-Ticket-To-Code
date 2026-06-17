import sqlite3

conn = sqlite3.connect(r'C:\CC4E\.aviator\index.db')
cur = conn.cursor()

print("=== How many style_of/template_of edges have NULL dst_id? ===")
cur.execute("SELECT kind, COUNT(*) FROM edges WHERE dst_id IS NULL GROUP BY kind")
print("NULL dst_id edges by type:")
for r in cur.fetchall(): print(f"  {r[0]}: {r[1]}")

cur.execute("SELECT kind, COUNT(*) FROM edges WHERE dst_id IS NOT NULL GROUP BY kind")
print("NON-NULL dst_id edges by type:")
for r in cur.fetchall(): print(f"  {r[0]}: {r[1]}")

print("\n=== Symbol ID resolution check ===")
# Pick a hash ID from imports and resolve it
cur.execute("SELECT src_id, dst_id FROM edges WHERE kind = 'imports' AND dst_id IS NOT NULL LIMIT 3")
sample_edges = cur.fetchall()
for src_id, dst_id in sample_edges:
    cur.execute("SELECT path, name, kind FROM symbols WHERE id = ?", (src_id,))
    src = cur.fetchone()
    cur.execute("SELECT path, name, kind FROM symbols WHERE id = ?", (dst_id,))
    dst = cur.fetchone()
    print(f"  {src} → {dst}")

print("\n=== How many calls edges have NULL dst? ===")
cur.execute("SELECT COUNT(*) FROM edges WHERE kind = 'calls' AND dst_id IS NULL")
print("calls with NULL dst:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM edges WHERE kind = 'calls' AND dst_id IS NOT NULL")
print("calls with valid dst:", cur.fetchone()[0])

conn.close()
