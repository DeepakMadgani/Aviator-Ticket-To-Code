import sqlite3
conn = sqlite3.connect(r"C:\CC4E\.aviator\index.db")

# Check TS files indexed
print("=== TypeScript files in index ===")
rows = conn.execute("SELECT path FROM files WHERE language='typescript' LIMIT 20").fetchall()
for r in rows: print(" ", r[0])
print("Total TS files:", conn.execute("SELECT COUNT(*) FROM files WHERE language='typescript'").fetchone()[0])

# Check if deliverable-reviewers exists
print("\n=== deliverable-reviewers files ===")
rows = conn.execute("SELECT path, language FROM files WHERE path LIKE '%deliverable-reviewer%'").fetchall()
for r in rows: print(" ", r)

# Check edges for TS files
print("\n=== Edge kinds for TS files ===")
rows = conn.execute("""
SELECT e.kind, COUNT(*) 
FROM edges e 
JOIN symbols s ON s.id = e.src_id 
JOIN files f ON f.path = s.path 
WHERE f.language = 'typescript' 
GROUP BY e.kind
""").fetchall()
for r in rows: print(" ", r)

# Check symbols for deliverable-reviewers
print("\n=== Symbols in deliverable-reviewers.component.ts ===")
rows = conn.execute("SELECT id, name, kind, spring_stereotype, annotations FROM symbols WHERE path LIKE '%deliverable-reviewer%component.ts%' LIMIT 10").fetchall()
for r in rows: print(" ", r)

# Check template_of / style_of edges
print("\n=== template_of / style_of edges (all) ===")
rows = conn.execute("SELECT e.kind, e.dst_name FROM edges e WHERE e.kind IN ('template_of','style_of') LIMIT 20").fetchall()
for r in rows: print(" ", r)

# Check all edges for deliverable-reviewers
print("\n=== All edges for deliverable-reviewers ===")
rows = conn.execute("""
SELECT e.kind, e.dst_name, e.dst_id
FROM edges e 
JOIN symbols s ON s.id = e.src_id 
WHERE s.path LIKE '%deliverable-reviewer%'
""").fetchall()
for r in rows: print(" ", r)

# Check language distribution  
print("\n=== Language distribution ===")
rows = conn.execute("SELECT language, COUNT(*) FROM files GROUP BY language ORDER BY 2 DESC").fetchall()
for r in rows: print(" ", r)

conn.close()
