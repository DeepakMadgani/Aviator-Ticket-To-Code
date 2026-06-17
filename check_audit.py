import sqlite3

conn = sqlite3.connect(r'C:\CC4E\.aviator\index.db')
cur = conn.cursor()

# 1. Is run-job.sh indexed?
cur.execute("SELECT path, language FROM files WHERE path LIKE '%run-job%'")
print("run-job files:", cur.fetchall())

# 2. Is app.component.ts indexed?
cur.execute("SELECT path, language FROM files WHERE path LIKE '%app.component.ts%' AND path NOT LIKE '%spec%'")
print("app.component files:", cur.fetchall())

# 3. What languages are in the index?
cur.execute("SELECT language, COUNT(*) as cnt FROM files GROUP BY language ORDER BY cnt DESC LIMIT 15")
print("\nLanguages in index:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

# 4. Is run-job.sh's extension indexed at all?
cur.execute("SELECT COUNT(*) FROM files WHERE path LIKE '%.sh'")
print("\n.sh files in index:", cur.fetchone()[0])

# 5. Symbol search for ghsHelpVersion
cur.execute("SELECT f.path, s.name, s.kind FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.name LIKE '%helpVersion%' OR s.name LIKE '%ghsHelp%'")
print("\nghsHelpVersion symbols:", cur.fetchall())

# 6. FTS search for 260200
try:
    cur.execute("SELECT f.path FROM symbols_fts sf JOIN symbols s ON sf.rowid = s.id JOIN files f ON s.file_id = f.id WHERE symbols_fts MATCH '260200'")
    print("\nFTS match 260200:", cur.fetchall())
except Exception as e:
    print("\nFTS 260200 error:", e)

# 7. Direct symbol name search
cur.execute("SELECT f.path, s.name FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.name = 'ghsHelpVersion'")
print("\nghsHelpVersion exact match:", cur.fetchall())

# 8. Check if app.component.ts has any symbols indexed
cur.execute("SELECT f.path, COUNT(s.id) as sym_count FROM files f LEFT JOIN symbols s ON s.file_id = f.id WHERE f.path LIKE '%app.component.ts%' AND f.path NOT LIKE '%spec%' GROUP BY f.path")
print("\napp.component.ts symbol count:", cur.fetchall())

conn.close()
