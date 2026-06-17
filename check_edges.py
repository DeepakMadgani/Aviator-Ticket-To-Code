import sqlite3
db = sqlite3.connect(r'C:\CC4E\.aviator\index.db')

rows = db.execute('SELECT kind, COUNT(*) as n FROM edges GROUP BY kind ORDER BY n DESC').fetchall()
print('Edge kinds:')
for k, n in rows:
    print(f'  {k}: {n}')
print()

rows = db.execute(
    "SELECT e.kind, e.dst_name, s.path FROM edges e "
    "JOIN symbols s ON s.id = e.src_id "
    "WHERE s.path LIKE '%jheader%' AND e.kind IN ('template_of','style_of')"
).fetchall()
print('jheader template/style edges:')
for k, dn, p in rows:
    print(f'  {p}')
    print(f'    --{k}-->  {dn}')
print()

rows = db.execute(
    "SELECT e.kind, e.dst_name, s.path FROM edges e "
    "JOIN symbols s ON s.id = e.src_id "
    "WHERE s.path LIKE '%deliverable-reviewers.component.ts%' AND e.kind IN ('template_of','style_of')"
).fetchall()
print('deliverable-reviewers template/style edges:')
for k, dn, p in rows:
    print(f'  {p}')
    print(f'    --{k}-->  {dn}')
print()

n1 = db.execute(
    "SELECT COUNT(*) FROM (SELECT DISTINCT s.path FROM edges e "
    "JOIN symbols s ON s.id = e.src_id WHERE e.kind='template_of')"
).fetchone()[0]
n2 = db.execute(
    "SELECT COUNT(*) FROM (SELECT DISTINCT s.path FROM edges e "
    "JOIN symbols s ON s.id = e.src_id WHERE e.kind='style_of')"
).fetchone()[0]
print(f'TS component files with template_of edges: {n1}')
print(f'TS component files with style_of edges: {n2}')

db.close()
