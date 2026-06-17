import sqlite3

conn = sqlite3.connect(r'C:\CC4E\.aviator\index.db')
cur = conn.cursor()

print("=== style_of edges (sample) ===")
cur.execute("SELECT src_id, dst_id, kind FROM edges WHERE kind = 'style_of' LIMIT 5")
rows = cur.fetchall()
for r in rows:
    src = str(r[0])[:70] if r[0] else "None"
    dst = str(r[1])[:70] if r[1] else "None"
    print(f"  {src} → {dst}")

print("\n=== template_of edges ===")
cur.execute("SELECT src_id, dst_id, kind FROM edges WHERE kind = 'template_of' LIMIT 5")
rows = cur.fetchall()
for r in rows:
    src = str(r[0])[:70] if r[0] else "None"
    dst = str(r[1])[:70] if r[1] else "None"
    print(f"  {src} → {dst}")

print("\n=== imports edges (sample) ===")
cur.execute("SELECT src_id, dst_id FROM edges WHERE kind = 'imports' LIMIT 5")
rows = cur.fetchall()
for r in rows:
    src = str(r[0])[:80] if r[0] else "None"
    dst = str(r[1])[:80] if r[1] else "None"
    print(f"  {src} → {dst}")

print("\n=== calls edges (sample) ===")
cur.execute("SELECT src_id, dst_id FROM edges WHERE kind = 'calls' LIMIT 5")
rows = cur.fetchall()
for r in rows:
    src = str(r[0])[:80] if r[0] else "None"
    dst = str(r[1])[:80] if r[1] else "None"
    print(f"  {src} → {dst}")

conn.close()
