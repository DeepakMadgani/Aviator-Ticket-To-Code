import sqlite3

conn = sqlite3.connect(r'C:\CC4E\.aviator\index.db')
cur = conn.cursor()

print("=== style_of edges (sample) ===")
cur.execute("SELECT src_id, dst_id, kind FROM edges WHERE kind = 'style_of' LIMIT 10")
for r in cur.fetchall(): print(f"  {r[0][:70]} → {r[1][:70]}")

print("\n=== template_of edges (sample) ===")
cur.execute("SELECT src_id, dst_id, kind FROM edges WHERE kind = 'template_of' LIMIT 10")
for r in cur.fetchall(): print(f"  {r[0][:70]} → {r[1][:70]}")

print("\n=== TS import edges (sample) ===")
cur.execute("SELECT src_id, dst_id, kind FROM edges WHERE kind = 'imports' LIMIT 10")
for r in cur.fetchall(): print(f"  {r[0][:70]} → {r[1][:70]}")

print("\n=== calls edges involving angular (sample) ===")
cur.execute("SELECT src_id, dst_id, kind FROM edges WHERE kind = 'calls' AND (src_id LIKE '%component%' OR dst_id LIKE '%component%') LIMIT 10")
for r in cur.fetchall(): print(f"  {r[0][:70]} → {r[1][:70]}")

print("\n=== Are edges stored as symbol IDs or file paths? ===")
cur.execute("SELECT src_id FROM edges LIMIT 3")
for r in cur.fetchall(): print("  src_id:", r[0])

# What does the generation node receive?
# Read the code_generator to understand the LLM prompt
print("\n=== CRITICAL: does the generation node receive the FILE CONTENT? ===")
# This is a code question - let's just confirm from the generation output

# What generation output looks like for footer ticket
# Already confirmed: generates 3 files for Ticket A
# But with what context?

# Let's check if the planner task has a 'description' that explains WHY
# (from the earlier trace we saw target_method: null, target_class: null)
# So the generator gets:
# 1. Task description (from planner)
# 2. File content (read from disk)  
# 3. RAG context (vector search results)
# But NOT:
# - The CSS property that is missing
# - The DOM parent-child relationship
# - The reason the footer is misplaced

conn.close()
