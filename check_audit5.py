import sqlite3

conn = sqlite3.connect(r'C:\CC4E\.aviator\index.db')
cur = conn.cursor()

cur.execute("SELECT path FROM files WHERE language = 'scss' LIMIT 10")
print("SCSS files in index:")
for r in cur.fetchall(): print(" ", r[0])

cur.execute("SELECT COUNT(*) FROM symbols WHERE path LIKE '%.scss'")
print("Total SCSS symbols:", cur.fetchone()[0])

cur.execute("SELECT COUNT(*) FROM edges WHERE src_id LIKE '%.scss' OR dst_id LIKE '%.scss'")
print("Edges involving scss:", cur.fetchone()[0])

cur.execute("SELECT kind, COUNT(*) FROM edges GROUP BY kind ORDER BY COUNT(*) DESC")
print("Edge types:")
for r in cur.fetchall(): print(f"  {r[0]}: {r[1]}")

print("\n=== app.component.ts: how many edges? ===")
cur.execute("SELECT COUNT(*) FROM edges WHERE src_id LIKE '%app.component%' OR dst_id LIKE '%app.component%'")
print("app.component edges:", cur.fetchone()[0])

print("\n=== HTML files: do they have edges? ===")
cur.execute("SELECT COUNT(*) FROM edges WHERE src_id LIKE '%.html' OR dst_id LIKE '%.html'")
print("HTML edges:", cur.fetchone()[0])

cur.execute("SELECT COUNT(*) FROM edges WHERE src_id LIKE '%.ts' OR dst_id LIKE '%.ts'")
print("TS edges:", cur.fetchone()[0])

conn.close()
