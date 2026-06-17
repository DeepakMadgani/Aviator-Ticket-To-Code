import sqlite3

conn = sqlite3.connect(r'C:\CC4E\.aviator\index.db')
cur = conn.cursor()

print("=== GENERATION INPUTS: What does the LLM receive for footer ticket? ===")
print("=== (proxy: what is in the RAG context for generation) ===\n")

# What does the localization agent know about deliverable-reviewers scss?
cur.execute("SELECT name, kind, start_line FROM symbols WHERE path LIKE '%deliverable-reviewers.component.scss%' LIMIT 20")
print("Symbols in deliverable-reviewers.component.scss:")
for r in cur.fetchall():
    print(f"  {r[1]}: {r[0]} (L{r[2]})")

cur.execute("SELECT name, kind, start_line FROM symbols WHERE path LIKE '%edit-deliverable.component.scss%' LIMIT 20")
print("\nSymbols in edit-deliverable.component.scss:")
for r in cur.fetchall():
    print(f"  {r[1]}: {r[0]} (L{r[2]})")

# What edges exist for the CSS files?
print("\n=== CSS edges ===")
cur.execute("""
    SELECT src_id, dst_id, kind FROM edges 
    WHERE src_id LIKE '%deliverable-reviewers%' OR dst_id LIKE '%deliverable-reviewers%'
    LIMIT 20
""")
for e in cur.fetchall():
    print(f"  {e[2]}: {e[0][:60]} → {e[1][:60]}")

# What does the GENERATION node receive as context?
# It receives: the task description, the file content, and RAG context
# The task description is created by the PLANNER
# Let's see what the planner writes for footer ticket

print("\n=== PLANNER TASK DESCRIPTIONS (from trace) ===")
# We'll read this from the trace

print("\n=== KEY QUESTION: Does any symbol know about flex-grow:1 requirement? ===")
cur.execute("SELECT name, kind, path FROM symbols WHERE name LIKE '%flex%' OR name LIKE '%overflow%' LIMIT 10")
print("flex/overflow symbols:", cur.fetchall())

# Does the index store CSS property information?
cur.execute("SELECT name, kind FROM symbols WHERE path LIKE '%.scss' LIMIT 20")
print("\nSCSS symbols (all):")
for r in cur.fetchall():
    print(f"  {r[1]}: {r[0]}")

conn.close()
